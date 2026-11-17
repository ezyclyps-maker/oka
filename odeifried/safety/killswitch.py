"""Kill switch. `odeifried stop` (or the Phase 3 hotkey) creates a STOP file and signals the agent.
The agent checks before every step and before every action, and refuses to continue while it exists."""
from __future__ import annotations

import os
import signal
from pathlib import Path


class Stopped(Exception):
    """Raised inside the agent loop when the kill switch is pressed."""


class KillSwitch:
    def __init__(self, state_dir: Path):
        self.flag = state_dir / "STOP"
        self.pidfile = state_dir / "agent.pid"

    def arm(self) -> None:
        """Called when a task starts: clears an old flag, records our PID."""
        self.flag.unlink(missing_ok=True)
        self.pidfile.write_text(str(os.getpid()))
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGUSR1, self._on_signal)

    def _on_signal(self, *_):
        self.flag.touch()
        raise Stopped("kill switch (signal)")

    def disarm(self) -> None:
        self.pidfile.unlink(missing_ok=True)

    def check(self) -> None:
        if self.flag.exists():
            raise Stopped("kill switch pressed")

    def press(self) -> bool:
        """Stop the running agent. Returns True if an agent process was signalled."""
        self.flag.touch()
        try:
            pid = int(self.pidfile.read_text())
            os.kill(pid, signal.SIGUSR1)
            return True
        except (OSError, ValueError):
            return False
