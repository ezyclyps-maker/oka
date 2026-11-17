"""Ask the human before any ASK-tier action. Shows the EXACT action. Default answer is NO."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Protocol


def describe(tool: str, args: dict) -> str:
    if tool == "run_shell":
        return f"Run command:\n  {args.get('command')}"
    if tool == "batch_move":
        lines = [f"  {m['src']}  ->  {m['dst']}" for m in args.get("moves", [])[:30]]
        more = len(args.get("moves", [])) - 30
        return "Move files:\n" + "\n".join(lines) + (f"\n  ...and {more} more" if more > 0 else "")
    return f"{tool}: {json.dumps(args, ensure_ascii=False)}"


class Confirmer(Protocol):
    def confirm(self, tool: str, args: dict, reason: str) -> bool: ...


class TerminalConfirmer:
    def confirm(self, tool: str, args: dict, reason: str) -> bool:
        print("\n" + "=" * 60)
        print("ODEIFRIED NEEDS YOUR PERMISSION")
        print(f"Why: {reason}")
        print(describe(tool, args))
        print("=" * 60)
        try:
            return input("Allow this? [y/N] ").strip().lower() in {"y", "yes"}
        except EOFError:
            return False


class ZenityConfirmer:
    """Graphical popup (used automatically when a desktop is available)."""

    def confirm(self, tool: str, args: dict, reason: str) -> bool:
        text = f"Why: {reason}\n\n{describe(tool, args)}"
        r = subprocess.run(["zenity", "--question", "--title=Odeifried: permission needed",
                            "--width=520", "--ok-label=Allow", "--cancel-label=Deny",
                            "--default-cancel", "--timeout=120", f"--text={text}"])
        return r.returncode == 0


class AlwaysDeny:
    def confirm(self, tool, args, reason) -> bool:
        return False


class AlwaysAllow:  # tests only
    def confirm(self, tool, args, reason) -> bool:
        return True


def default_confirmer() -> Confirmer:
    if os.environ.get("DISPLAY") and shutil.which("zenity") and os.environ.get("ODEIFRIED_CONFIRM") != "terminal":
        return ZenityConfirmer()
    return TerminalConfirmer()
