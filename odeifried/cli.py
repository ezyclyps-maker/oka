"""Command line: odeifried run "task" | doctor | stop | undo | log"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

from . import config, hardware
from .journal import undo_last
from .llm import LLMError, Ollama
from .safety.killswitch import KillSwitch


def _llm(cfg) -> Ollama:
    return Ollama(cfg.get("models", "ollama_url"), float(cfg.get("models", "temperature", 0.1)),
                  int(cfg.get("models", "timeout_seconds", 300)), bool(cfg.get("models", "think", False)))


def cmd_doctor(cfg) -> int:
    hw = hardware.detect()
    rec = hardware.recommend(hw)
    print(f"RAM: {hw.ram_gb} GB   CPU threads: {hw.cpu_threads}   GPU: {hw.gpu_name or 'none (CPU inference)'}"
          + (f" {hw.vram_gb} GB" if hw.vram_gb else ""))
    print("  (inside a VM this shows the VM's resources; models run wherever Ollama runs)")
    print(f"Recommended: planner={rec['planner']} vision={rec['vision']}  - {rec['note']}")
    print(f"Configured:  planner={cfg.get('models', 'planner')} vision={cfg.get('models', 'vision')}")
    ok = True
    try:
        have = _llm(cfg).models()
        print(f"Ollama: reachable at {cfg.get('models', 'ollama_url')}")
        for role in ("planner", "vision"):
            m = cfg.get("models", role)
            found = any(h == m or h == m + ":latest" for h in have)
            print(f"  {role:8} {m}: {'installed' if found else 'MISSING -> run on the host: ollama pull ' + m}")
            ok &= found or role == "vision"
    except Exception as e:  # noqa: BLE001
        print(f"Ollama: NOT reachable at {cfg.get('models', 'ollama_url')} ({e})")
        ok = False
    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    print(f"Desktop session: {session}" + ("" if session == "x11" else "  (choose 'Ubuntu on Xorg' for GUI control)"))
    for tool in ("xdotool", "scrot", "convert", "zenity", "gtk-launch"):
        print(f"  {tool:11} {'ok' if shutil.which(tool) else 'missing'}")
    try:
        import gi  # noqa: F401
        print("  AT-SPI     ok (python3-gi)")
    except ImportError:
        print("  AT-SPI     missing (create the venv with --system-site-packages)")
    print(f"Logs: {cfg.log_dir}")
    return 0 if ok else 1


def cmd_run(cfg, task: str, gui: bool) -> int:
    from .agent import Agent
    from .safety.confirm import default_confirmer
    print(f"Odeifried: {task}\n(press Ctrl+C or run `odeifried stop` to stop)\n")
    try:
        res = Agent(cfg, _llm(cfg), default_confirmer(), gui=gui).run(task)
    except LLMError as e:
        print(f"Model error: {e}")
        return 2
    except KeyboardInterrupt:
        print("\nStopped. Use `odeifried undo` to reverse changes.")
        return 130
    print(f"\n{'DONE' if res.ok else 'NOT DONE'} in {res.steps} steps: {res.summary}")
    print(f"Log: {cfg.log_dir} (task {res.task_id})")
    return 0 if res.ok else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in {"run", "doctor", "stop", "undo", "log", "-h", "--help"}:
        argv = ["run", " ".join(argv)]      # `odeifried organize my downloads` works too
    p = argparse.ArgumentParser(prog="odeifried")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="give the AI a task")
    r.add_argument("task")
    r.add_argument("--no-gui", action="store_true", help="files + shell only")
    sub.add_parser("doctor", help="check hardware, models and desktop tools")
    sub.add_parser("stop", help="kill switch: stop the running agent now")
    sub.add_parser("undo", help="reverse the file changes of the last task")
    sub.add_parser("log", help="show the latest log entries")
    a = p.parse_args(argv)
    cfg = config.load()
    if a.cmd == "doctor":
        return cmd_doctor(cfg)
    if a.cmd == "run":
        return cmd_run(cfg, a.task, gui=not a.no_gui)
    if a.cmd == "stop":
        print("Agent stopped." if KillSwitch(cfg.state_dir).press() else "Stop flag set (no agent was running).")
        return 0
    if a.cmd == "undo":
        print("\n".join(undo_last(cfg.state_dir)))
        return 0
    if a.cmd == "log":
        files = sorted(cfg.log_dir.glob("actions-*.jsonl"))
        print("".join(files[-1].read_text().splitlines(keepends=True)[-30:]) if files else "No logs yet.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
