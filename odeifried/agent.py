"""The agent loop: observe -> plan -> act -> verify, with every action passing through the safety policy."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .audit import AuditLog
from .config import Config
from .journal import Journal
from .llm import tool_calls
from .safety.confirm import Confirmer
from .safety.killswitch import KillSwitch, Stopped
from .safety.policy import Policy, Tier
from .tools import ToolContext, all_tools

SYSTEM = """You are Odeifried, the AI that operates this Linux computer for the user.
Finish the user's task by calling tools, one step at a time. Rules:
- Prefer direct tools (file tools, organize_by_type, web_search, open_app, run_shell) over clicking.
- For apps: list_ui to see numbered buttons/fields, then click_element / type_text. Use describe_and_click only if list_ui can't find it.
- Tool results starting with VERIFIED are confirmed. If a result says ERROR, DENIED or REFUSED, adapt; never retry a refused action.
- Use absolute paths. The user's home is {home}.
- When finished, call done with a short summary. Do not ask the user questions; do the task."""


@dataclass
class Result:
    ok: bool
    summary: str
    steps: int
    task_id: str


class Agent:
    def __init__(self, cfg: Config, llm, confirmer: Confirmer, gui: bool = True, echo=print):
        self.cfg, self.llm, self.confirmer, self.echo = cfg, llm, confirmer, echo
        self.policy = Policy.from_config(cfg)
        self.tools = all_tools(gui=gui)
        self.killswitch = KillSwitch(cfg.state_dir)

    def _enrich(self, ctx: ToolContext, name: str, args: dict) -> dict:
        """The policy must judge the REAL element, not the model's claim about it."""
        if name in {"click_element", "type_text"} and args.get("element_id") is not None:
            acc = ctx.ui_cache.get(int(args["element_id"]))
            if acc is not None:
                try:
                    args = {**args, "name": acc.get_name() or ""}
                except Exception:  # noqa: BLE001
                    pass
        return args

    def execute(self, ctx: ToolContext, log: AuditLog, name: str, args: dict) -> str:
        """One action through the safety gate. Returns the text the model sees."""
        args = self._enrich(ctx, name, args)
        decision = self.policy.check(name, args)
        log.write("decision", tool=name, args=args, tier=decision.tier.name, reason=decision.reason)
        if decision.tier == Tier.NEVER:
            self.echo(f"  ✖ REFUSED {name}: {decision.reason}")
            return f"REFUSED (never allowed): {decision.reason}"
        if decision.tier == Tier.ASK:
            allowed = self.confirmer.confirm(name, args, decision.reason)
            log.write("confirmation", tool=name, allowed=allowed)
            if not allowed:
                self.echo(f"  ✖ DENIED by you: {name}")
                return "DENIED by the user. Do not try this again."
        self.killswitch.check()
        tool = self.tools.get(name)
        try:
            t0 = time.time()
            out = tool.fn(ctx, **{k: v for k, v in args.items() if k in tool.params})
            log.write("result", tool=name, ok=True, seconds=round(time.time() - t0, 2), output=str(out)[:2000])
            return str(out)
        except Stopped:
            raise
        except Exception as e:  # noqa: BLE001 - report every tool failure back to the model
            log.write("result", tool=name, ok=False, error=repr(e))
            return f"ERROR: {type(e).__name__}: {e}"

    def run(self, task: str) -> Result:
        task_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        log = AuditLog(self.cfg.log_dir, task_id)
        ctx = ToolContext(self.cfg, Journal(self.cfg.state_dir, task_id), llm=self.llm)
        model = self.cfg.get("models", "planner")
        schemas = [t.schema() for t in self.tools.values()]
        messages = [{"role": "system", "content": SYSTEM.format(home=Path.home())},
                    {"role": "user", "content": task}]
        log.write("task_start", task=task, model=model)
        self.killswitch.arm()
        step = 0
        try:
            for step in range(1, int(self.cfg.get("agent", "max_steps", 25)) + 1):
                self.killswitch.check()
                t0 = time.time()
                msg = self.llm.chat(model, messages, schemas)
                calls = tool_calls(msg)
                log.write("plan", step=step, seconds=round(time.time() - t0, 1),
                          thought=msg.get("content", "")[:1000], calls=calls)
                messages.append({"role": "assistant", "content": msg.get("content", ""),
                                 **({"tool_calls": msg["tool_calls"]} if msg.get("tool_calls") else {})})
                if not calls:
                    # Model answered in plain text: nudge once, then accept it as the final answer.
                    if step > 1 and messages[-2].get("role") == "user":
                        break
                    messages.append({"role": "user", "content": "Use the tools to do the task, then call done."})
                    continue
                for name, args in calls:
                    self.echo(f"[{step}] {name} {json.dumps(args, ensure_ascii=False)[:160]}")
                    if name == "done":
                        summary = args.get("summary", "")
                        log.write("task_end", ok=True, summary=summary, steps=step)
                        return Result(True, summary, step, task_id)
                    out = self.execute(ctx, log, name, args)
                    self.echo("    → " + out.splitlines()[0][:160] if out else "    → (no output)")
                    messages.append({"role": "tool", "tool_name": name, "content": out})
            log.write("task_end", ok=False, summary="step limit or no progress", steps=step)
            return Result(False, "Stopped: step limit reached or the model stopped using tools.", step, task_id)
        except Stopped as e:
            log.write("task_end", ok=False, summary=f"KILLED: {e}", steps=step)
            return Result(False, f"Stopped by kill switch ({e}). Use `odeifried undo` to reverse changes.", step, task_id)
        finally:
            self.killswitch.disarm()
