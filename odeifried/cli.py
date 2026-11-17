"""Command line.

  odeifried "organize my downloads, then play lil baby on spotify"
  odeifried chat "explain photosynthesis"      talk to the chat model (changes nothing on the computer)
  odeifried bar                                open the Intent Bar (what Super+Space shows)
  odeifried doctor | stop | undo | log | skills
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

from . import config, engine, hardware
from .journal import undo_last
from .llm import LLMError, Ollama, has_model
from .safety.killswitch import KillSwitch

COMMANDS = {"run", "chat", "bar", "doctor", "stop", "undo", "log", "skills", "-h", "--help"}


def make_llm(cfg) -> Ollama:
    return Ollama(engine.resolve_url(cfg), float(cfg.get("models", "temperature", 0.1)),
                  int(cfg.get("models", "timeout_seconds", 300)), bool(cfg.get("models", "think", False)),
                  str(cfg.get("models", "keep_alive", "2h")))


class JsonConfirmer:
    """Permission questions for a graphical front end: asks on stdout, reads the answer from stdin."""

    def __init__(self):
        self.n = 0

    def confirm(self, tool: str, args: dict, reason: str) -> bool:
        from .safety.confirm import describe
        self.n += 1
        print(json.dumps({"type": "confirm", "id": self.n, "tool": tool, "reason": reason,
                          "text": describe(tool, args)}), flush=True)
        for line in sys.stdin:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("confirm") == self.n:
                return bool(msg.get("allow"))
        return False                                   # front end went away: the answer is no


def cmd_doctor(cfg) -> int:
    hw = hardware.detect()
    rec = hardware.recommend(hw)
    print(f"RAM: {hw.ram_gb} GB   CPU threads: {hw.cpu_threads}   GPU: {hw.gpu_name or 'none (models run on the CPU)'}"
          + (f" {hw.vram_gb} GB" if hw.vram_gb else ""))
    print(f"Recommended models: agent={rec['agent']} vision={rec['vision']}  - {rec['note']}")
    print(f"Configured models:  agent={cfg.get('models', 'agent')} chat={cfg.get('models', 'chat') or '(same as agent)'} "
          f"vision={cfg.get('models', 'vision')}")
    found = engine.find(cfg, use_cache=False)
    ok = found is not None
    if found:
        url, have = found
        where = "this computer" if "127.0.0.1" in url or "localhost" in url else "the computer hosting this virtual machine"
        print(f"AI engine: found at {url} ({where})")
        for role in ("agent", "chat", "vision"):
            m = cfg.get("models", role)
            if not m:
                continue
            present = has_model(have, m)
            print(f"  {role:7} {m}: {'installed' if present else 'MISSING -> where Ollama runs:  ollama pull ' + m}")
            ok &= present or role != "agent"
    else:
        print("AI engine: NOT found. Instant commands still work; anything needing the AI model will not.")
        print("  In a virtual machine: install Ollama on the host computer and run:  ollama pull " + cfg.get("models", "agent"))
    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    print(f"Desktop session: {session}" + ("" if session == "x11" else "  (screen control needs an X11 session)"))
    for tool in ("xdotool", "gdbus", "gtk-launch", "playerctl", "wpctl", "flatpak"):
        print(f"  {tool:11} {'ok' if shutil.which(tool) else 'missing'}")
    try:
        import gi  # noqa: F401
        print("  python-gi   ok (accessibility + Intent Bar)")
    except ImportError:
        print("  python-gi   missing (sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-atspi-2.0)")
    print(f"Logs: {cfg.log_dir}")
    return 0 if ok else 1


def cmd_run(cfg, task: str, gui: bool, events: bool) -> int:
    from .agent import Assistant
    from .safety.confirm import default_confirmer
    if events:
        emit = lambda e: print(json.dumps(e, ensure_ascii=False), flush=True)  # noqa: E731
        res = Assistant(cfg, make_llm(cfg), JsonConfirmer(), gui=gui, emit=emit).handle(task)
        return 0 if res.ok else 1
    print("(Ctrl+C or `odeifried stop` stops me at any time)")
    try:
        res = Assistant(cfg, make_llm(cfg), default_confirmer(), gui=gui, echo=print).handle(task)
    except KeyboardInterrupt:
        print("\nStopped. `odeifried undo` reverses file changes.")
        return 130
    print(f"\n{'DONE' if res.ok else 'NOT DONE'} in {res.seconds:.1f}s: {res.summary}")
    return 0 if res.ok else 1


def cmd_chat(cfg, text: str, events: bool) -> int:
    from .agent import Assistant
    from .safety.confirm import AlwaysDeny
    emit = (lambda e: print(json.dumps(e, ensure_ascii=False), flush=True)) if events else None
    try:
        Assistant(cfg, make_llm(cfg), AlwaysDeny(), gui=False, emit=emit, echo=None if events else print).chat(text)
    except LLMError as e:
        print(json.dumps({"type": "done", "ok": False, "summary": str(e)}) if events else f"\n{e}")
        return 2
    if not events:
        print()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS:
        argv = ["run", " ".join(argv)]      # `odeifried organize my downloads` works too
    p = argparse.ArgumentParser(prog="odeifried", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="give the AI a task")
    r.add_argument("task")
    r.add_argument("--no-gui", action="store_true", help="no screen control (files, shell, instant commands only)")
    r.add_argument("--events", action="store_true", help=argparse.SUPPRESS)
    c = sub.add_parser("chat", help="talk to the chat model")
    c.add_argument("text")
    c.add_argument("--events", action="store_true", help=argparse.SUPPRESS)
    b = sub.add_parser("bar", help="open the Intent Bar")
    b.add_argument("--gapplication-service", action="store_true", help=argparse.SUPPRESS)
    for name, text in (("doctor", "check hardware, AI engine, models and desktop tools"),
                       ("stop", "kill switch: stop the AI now"), ("undo", "reverse the file changes of the last task"),
                       ("log", "show the latest log entries"), ("skills", "show what the AI has learned to do instantly")):
        sub.add_parser(name, help=text)
    a = p.parse_args(argv)
    cfg = config.load()
    if a.cmd == "doctor":
        return cmd_doctor(cfg)
    if a.cmd == "run":
        return cmd_run(cfg, a.task, not a.no_gui, a.events)
    if a.cmd == "chat":
        return cmd_chat(cfg, a.text, a.events)
    if a.cmd == "bar":
        from .bar import main as bar_main
        return bar_main(["odeifried-bar"] + (["--gapplication-service"] if a.gapplication_service else []))
    if a.cmd == "stop":
        print("AI stopped." if KillSwitch(cfg.state_dir).press() else "Stop flag set (the AI was not running).")
        return 0
    if a.cmd == "undo":
        print("\n".join(undo_last(cfg.state_dir)))
        return 0
    if a.cmd == "log":
        files = sorted(cfg.log_dir.glob("actions-*.jsonl"))
        print("".join(files[-1].read_text().splitlines(keepends=True)[-30:]) if files else "No logs yet.")
        return 0
    if a.cmd == "skills":
        from .memory import Memory
        rows = Memory(cfg.state_dir / "memory.db").skills()
        print("\n".join(f"{uses:3d}x  {req}" for req, uses, _ in rows) or "Nothing learned yet.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
