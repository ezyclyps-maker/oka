"""The brain of Odeifried.

  Assistant.handle("organize my files, then play lil baby on spotify")
      1. splits the request into single tasks
      2. for each task picks the fastest route that works:
           instant  - a recognized everyday command (no AI model, well under a second)
           skill    - a task the AI solved before, replayed from memory
           ai       - the AI model plans step by step with tools (observe -> plan -> act -> verify)
      3. EVERY action, on every route, passes the safety policy first, is logged, and is undoable.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import router
from .audit import AuditLog
from .config import Config
from .journal import Journal
from .llm import LLMError, tool_calls
from .memory import Memory
from .safety.confirm import Confirmer, describe
from .safety.killswitch import KillSwitch, Stopped
from .safety.policy import Policy, Tier
from .tools import ToolContext, all_tools

SYSTEM = """You are Odeifried, the AI built into this computer's operating system. You operate the computer for the user by calling tools.
Rules:
- Do the task with as few tool calls as possible. Prefer direct tools (organize_by_type, batch_move, web_search, open_app, open_url, play_music, set_volume, install_app, run_shell) over clicking.
- To use an app's window: list_ui shows numbered buttons and fields, then click_element / type_text. Use describe_and_click only if list_ui cannot find it.
- A result starting with VERIFIED is confirmed done. If a result says ERROR, DENIED or REFUSED, try a different way; never repeat a denied or refused action.
- Use absolute paths. The user's home folder is {home}.
- When the task is finished (or impossible) call done with one short sentence for the user. Do not ask the user questions.
- If the user only asks a question, answer it in plain text without tools.
Examples: "sort my downloads" -> organize_by_type(path="{home}/Downloads"). "look up cheap flights" -> web_search(query="cheap flights"). "put on some drake" -> play_music(query="drake")."""

CHAT_SYSTEM = "You are Odeifried, the assistant built into the user's computer. Answer directly and concisely."
_QUESTION = re.compile(r"^(who|what|why|how|when|where|which|is|are|was|were|do|does|did|can|could|should|would|will|"
                       r"explain|tell me|describe|define|write|summari[sz]e|translate|give me|list)\b", re.I)
_FAILED = ("ERROR", "DENIED", "REFUSED", "NOT FOUND")


@dataclass
class Result:
    ok: bool
    summary: str
    steps: int
    task_id: str
    route: str = "ai"
    seconds: float = 0.0


@dataclass
class Session:
    """One user request (possibly several chained tasks): shares a log, an undo journal and tool state."""
    task_id: str
    log: AuditLog
    ctx: ToolContext
    steps: int = 0
    trail: list = field(default_factory=list)       # (tool, args, output) of everything executed


def is_failure(output: str) -> bool:
    return output.startswith(_FAILED)


def friendly(output: str) -> str:
    """Tool output -> a line fit for a human."""
    line = (output or "").strip().splitlines()[0] if output and output.strip() else "done"
    line = re.sub(r"^(VERIFIED|PARTIAL)\s+", "", line)
    return line[0].upper() + line[1:] if line else "Done"


class Agent:
    """Runs actions through the safety gate, and drives the AI model when one is needed."""

    def __init__(self, cfg: Config, llm, confirmer: Confirmer, gui: bool = True,
                 emit: Callable[[dict], None] | None = None, echo=None):
        self.cfg, self.llm, self.confirmer = cfg, llm, confirmer
        self.emit = emit or (lambda event: None)
        if echo and not emit:                        # plain-text progress for the terminal
            self.emit = _printer(echo)
        self.policy = Policy.from_config(cfg)
        self.tools = all_tools(gui=gui)
        self.llm_tools = {n: t for n, t in self.tools.items() if t.llm}
        self.killswitch = KillSwitch(cfg.state_dir)
        self.memory = Memory(cfg.state_dir / "memory.db")

    # ---- sessions
    def new_session(self) -> Session:
        task_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        ctx = ToolContext(self.cfg, Journal(self.cfg.state_dir, task_id), llm=self.llm, memory=self.memory)
        return Session(task_id, AuditLog(self.cfg.log_dir, task_id), ctx)

    @property
    def model(self) -> str:
        return self.cfg.get("models", "agent") or self.cfg.get("models", "planner")

    def prompt_prefix(self) -> tuple[list[dict], list[dict]]:
        """The fixed start of every AI request. Kept identical so the engine can cache it."""
        return ([{"role": "system", "content": SYSTEM.format(home=Path.home())}],
                [t.schema() for t in self.llm_tools.values()])

    def warm(self) -> None:
        """Load the model and pre-read the fixed prompt so the first real task starts fast."""
        messages, schemas = self.prompt_prefix()
        self.llm.warm(self.model, messages + [{"role": "user", "content": "hello"}], schemas)

    # ---- the safety gate: the ONLY way any action runs
    def _enrich(self, ctx: ToolContext, name: str, args: dict) -> dict:
        """The policy must judge the REAL element, not the model's claim about it."""
        if name in {"click_element", "type_text"} and args.get("element_id") is not None:
            try:
                acc = ctx.ui_cache.get(int(args["element_id"]))
                if acc is not None:
                    args = {**args, "name": acc.get_name() or ""}
            except Exception:  # noqa: BLE001
                pass
        return args

    def execute(self, s: Session, name: str, args: dict) -> str:
        """One action through the safety gate. Returns the result text."""
        args = self._enrich(s.ctx, name, dict(args or {}))
        decision = self.policy.check(name, args)
        s.steps += 1
        s.log.write("decision", tool=name, args=args, tier=decision.tier.name, reason=decision.reason)
        self.emit({"type": "step", "tool": name, "args": args, "tier": decision.tier.name})
        if decision.tier == Tier.NEVER:
            out = f"REFUSED (never allowed): {decision.reason}"
            self.emit({"type": "refused", "tool": name, "reason": decision.reason})
            s.trail.append((name, args, out))
            return out
        if decision.tier == Tier.ASK:
            allowed = self.confirmer.confirm(name, args, decision.reason)
            s.log.write("confirmation", tool=name, allowed=allowed)
            if not allowed:
                out = "DENIED by the user. Do not try this again."
                self.emit({"type": "denied", "tool": name, "text": describe(name, args)})
                s.trail.append((name, args, out))
                return out
        self.killswitch.check()
        tool = self.tools[name]
        try:
            t0 = time.time()
            out = str(tool.fn(s.ctx, **{k: v for k, v in args.items() if k in tool.params}))
            s.log.write("result", tool=name, ok=not is_failure(out), seconds=round(time.time() - t0, 2), output=out[:2000])
        except Stopped:
            raise
        except Exception as e:  # noqa: BLE001 - report every tool failure back instead of crashing
            out = f"ERROR: {type(e).__name__}: {e}"
            s.log.write("result", tool=name, ok=False, error=repr(e))
        self.emit({"type": "output", "tool": name, "text": friendly(out), "ok": not is_failure(out)})
        s.trail.append((name, args, out))
        return out

    # ---- route 3: the AI model plans step by step
    def run_llm(self, s: Session, task: str) -> Result:
        t_start = time.time()
        prefix, schemas = self.prompt_prefix()
        messages = prefix + [{"role": "user", "content": task}]
        known = set(self.llm_tools)
        max_steps = int(self.cfg.get("agent", "max_steps", 25))
        nudged = False
        for step in range(1, max_steps + 1):
            self.killswitch.check()
            self.emit({"type": "thinking"})
            t0 = time.time()
            msg = self.llm.chat(self.model, messages, schemas)
            calls = tool_calls(msg, known)
            text = (msg.get("content") or "").strip()
            s.log.write("plan", step=step, seconds=round(time.time() - t0, 1), thought=text[:1000], calls=calls)
            messages.append({"role": "assistant", "content": msg.get("content", ""),
                             **({"tool_calls": msg["tool_calls"]} if msg.get("tool_calls") else {})})
            if not calls:
                # Plain text. A question deserves a plain answer; a task deserves one reminder to act.
                if text and (nudged or _QUESTION.match(task.strip()) or task.strip().endswith("?")):
                    return Result(True, text, step, s.task_id, "ai", time.time() - t_start)
                if nudged:
                    break
                nudged = True
                messages.append({"role": "user", "content": "Use the tools to do the task, then call done."})
                continue
            for name, args in calls:
                if name == "done":
                    return Result(True, str(args.get("summary", "Done.")), step, s.task_id, "ai", time.time() - t_start)
                if name not in self.llm_tools:
                    out = f"REFUSED: there is no tool called '{name}'"
                    s.log.write("decision", tool=name, args=args, tier="NEVER", reason="unknown tool")
                    self.emit({"type": "refused", "tool": name, "reason": "unknown tool"})
                else:
                    out = self.execute(s, name, args)
                messages.append({"role": "tool", "tool_name": name, "content": out})
        return Result(False, "I ran out of steps before finishing this.", max_steps, s.task_id, "ai", time.time() - t_start)

    # ---- kept for the simple case and for tests: one task, AI route only
    def run(self, task: str) -> Result:
        s = self.new_session()
        s.log.write("task_start", task=task, model=self.model)
        self.killswitch.arm()
        try:
            res = self.run_llm(s, task)
        except Stopped as e:
            res = Result(False, f"Stopped by kill switch ({e}). Use `odeifried undo` to reverse changes.", s.steps, s.task_id)
        finally:
            self.killswitch.disarm()
        s.log.write("task_end", ok=res.ok, summary=res.summary, steps=res.steps)
        return res


class Assistant(Agent):
    """What the Intent Bar and the `odeifried` command talk to."""

    def _instant(self, s: Session, plan: router.Plan) -> Result:
        t0, outputs = time.time(), []
        for name, args in plan.steps:
            out = self.execute(s, name, args)
            outputs.append(out)
            if is_failure(out):
                return Result(False, friendly(out), len(outputs), s.task_id, "instant", time.time() - t0)
        return Result(True, " ".join(friendly(o) for o in outputs), len(outputs), s.task_id, "instant", time.time() - t0)

    def _replay(self, s: Session, task: str, steps: list[tuple[str, dict]]) -> Result | None:
        """Replay a learned skill. Returns None if it no longer works (caller falls back to the AI)."""
        t0, last = time.time(), ""
        for name, args in steps:
            last = self.execute(s, name, args)
            if is_failure(last):
                if last.startswith("DENIED"):
                    return Result(False, "Cancelled.", s.steps, s.task_id, "skill", time.time() - t0)
                self.memory.forget(task)
                return None
        self.memory.used(task)
        return Result(True, friendly(last), len(steps), s.task_id, "skill", time.time() - t0)

    def _one(self, s: Session, task: str) -> Result:
        plan = router.match(task)
        if plan:
            self.emit({"type": "route", "route": "instant", "text": task})
            return self._instant(s, plan)
        if self.cfg.get("agent", "learn_skills", True) and (steps := self.memory.recall(task)):
            self.emit({"type": "route", "route": "skill", "text": task})
            if (res := self._replay(s, task, steps)) is not None:
                return res
        self.emit({"type": "route", "route": "ai", "text": task})
        mark = len(s.trail)
        try:
            res = self.run_llm(s, task)
        except LLMError as e:
            return Result(False, f"I need the AI model for this one, but {e}. Run `odeifried doctor` or open "
                                 f"\"Odeifried Setup\".", 0, s.task_id, "ai")
        mine = s.trail[mark:]
        if res.ok and mine and not any(is_failure(o) for _, _, o in mine) and self.cfg.get("agent", "learn_skills", True):
            if self.memory.learn(task, [(n, a) for n, a, _ in mine]):
                s.log.write("skill_learned", request=task, steps=len(mine))
        return res

    def handle(self, text: str) -> Result:
        """Do everything the user asked for in one message."""
        t0 = time.time()
        s = self.new_session()
        tasks = router.split_tasks(text) or [router.clean(text)]
        s.log.write("task_start", task=text, parts=tasks, model=self.model)
        self.emit({"type": "start", "task_id": s.task_id, "tasks": tasks})
        self.killswitch.arm()
        results: list[Result] = []
        try:
            for i, task in enumerate(tasks, 1):
                self.killswitch.check()
                self.emit({"type": "task", "index": i, "total": len(tasks), "text": task})
                res = self._one(s, task)
                results.append(res)
                self.emit({"type": "task_done", "index": i, "ok": res.ok, "summary": res.summary, "route": res.route})
        except Stopped as e:
            results.append(Result(False, f"Stopped ({e}). Say \"undo\" to reverse file changes.", s.steps, s.task_id, "stopped"))
        finally:
            self.killswitch.disarm()
        ok = bool(results) and all(r.ok for r in results)
        if len(results) == 1:
            summary = results[0].summary
        else:
            summary = "\n".join(f"{'✓' if r.ok else '✗'} {t}: {r.summary}" for t, r in zip(tasks, results))
        route = results[0].route if len({r.route for r in results}) == 1 else "mixed"
        final = Result(ok, summary, s.steps, s.task_id, route, round(time.time() - t0, 2))
        s.log.write("task_end", ok=ok, summary=summary, steps=s.steps, route=route, seconds=final.seconds)
        try:
            self.memory.record_task(s.task_id, text, route, ok, summary, final.seconds)
        except Exception:  # noqa: BLE001 - history is nice-to-have
            pass
        self.emit({"type": "done", "ok": ok, "summary": summary, "route": route, "seconds": final.seconds,
                   "task_id": s.task_id})
        return final

    def chat(self, text: str, history: list[dict] | None = None) -> str:
        """Plain conversation with the chat model (no tools, nothing on the computer changes)."""
        model = self.cfg.get("models", "chat") or self.model
        messages = [{"role": "system", "content": CHAT_SYSTEM}] + list(history or []) + [{"role": "user", "content": text}]
        self.emit({"type": "start", "tasks": [text], "chat": True, "model": model})
        reply = []
        for piece in self.llm.chat_stream(model, messages):
            reply.append(piece)
            self.emit({"type": "token", "text": piece})
        answer = "".join(reply).strip()
        self.emit({"type": "done", "ok": True, "summary": answer, "route": "chat", "chat": True})
        return answer


def _printer(echo) -> Callable[[dict], None]:
    """Turn events into readable terminal lines."""
    def emit(e: dict) -> None:
        kind = e.get("type")
        if kind == "task" and e.get("total", 1) > 1:
            echo(f"\n[{e['index']}/{e['total']}] {e['text']}")
        elif kind == "route":
            echo(f"  ({'instant' if e['route'] == 'instant' else 'from memory' if e['route'] == 'skill' else 'asking the AI model'})")
        elif kind == "step":
            echo(f"  • {e['tool']} {json.dumps(e['args'], ensure_ascii=False)[:150]}")
        elif kind == "output":
            echo(f"    → {e['text'][:200]}")
        elif kind == "refused":
            echo(f"    ✖ REFUSED: {e['reason']}")
        elif kind == "denied":
            echo("    ✖ DENIED by you")
        elif kind == "token":
            echo(e["text"], end="", flush=True)
    return emit
