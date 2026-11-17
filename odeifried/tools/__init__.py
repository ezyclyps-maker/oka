"""Tool registry. A tool = a Python function + a JSON schema the model sees.
Tools never decide permissions themselves: the agent asks safety.Policy first."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable

from ..config import Config
from ..journal import Journal


@dataclass
class ToolContext:
    cfg: Config
    journal: Journal
    llm: object | None = None                     # used by the vision fallback
    memory: object | None = None                  # small cache/skill store (may be None)
    ui_cache: dict = field(default_factory=dict)  # element id -> AT-SPI accessible


@dataclass
class Tool:
    name: str
    description: str
    params: dict                      # JSON-schema "properties"
    required: list[str]
    fn: Callable[..., str]
    llm: bool = True                  # False = only the instant router uses it (keeps the AI's menu short)

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.params, "required": self.required}}}


def all_tools(gui: bool = True) -> dict[str, Tool]:
    from . import files, music, shell, system
    tools = files.TOOLS + shell.TOOLS + system.TOOLS + music.TOOLS
    if gui:
        from ..control import desktop
        tools += desktop.TOOLS
    tools.append(Tool("done", "Call when the task is finished (or impossible). Summarize what you did.",
                      {"summary": {"type": "string"}}, ["summary"], lambda ctx, summary: summary))
    return {t.name: t for t in tools}


# ---- helpers shared by tools
def have(program: str) -> bool:
    return shutil.which(program) is not None


def run(argv: list[str], timeout: float = 15) -> tuple[int, str]:
    """Run a helper program; returns (exit code, combined output). Never raises for a missing program."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return 127, f"{argv[0]}: not installed"
    except subprocess.TimeoutExpired:
        return 124, f"{argv[0]}: timed out"


_PASS_ENV = ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS",
             "XDG_CURRENT_DESKTOP", "XDG_CONFIG_DIRS", "XDG_DATA_DIRS", "XDG_SESSION_TYPE")


def launch_detached(argv: list[str]) -> None:
    """Start an app so it belongs to the user's session, not to the agent: it keeps running if the
    agent is stopped, and it does not inherit the agent's "can never become admin" restriction."""
    if have("systemd-run") and os.environ.get("XDG_RUNTIME_DIR"):
        env = [f"--setenv={k}" for k in _PASS_ENV if os.environ.get(k)]
        rc, _ = run(["systemd-run", "--user", "--quiet", "--collect", "--no-block", *env, *argv], timeout=10)
        if rc == 0:
            return
    subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, start_new_session=True)
