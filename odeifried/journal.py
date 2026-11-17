"""Undo journal. Every file change the agent makes is recorded; `odeifried undo` reverses the last task.
(Phase 5 adds btrfs snapshots for whole-system rollback; this covers the agent's own file actions.)"""
from __future__ import annotations

import json
import shutil
from pathlib import Path


class Journal:
    def __init__(self, state_dir: Path, task_id: str):
        self.dir = state_dir / "journal"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{task_id}.json"
        self.ops: list[dict] = []

    def record(self, op: str, **data) -> None:
        self.ops.append({"op": op, **data})
        self.path.write_text(json.dumps(self.ops, indent=1))
        (self.dir / "LAST").write_text(self.path.name)


def undo_last(state_dir: Path) -> list[str]:
    """Reverse the most recent task's file operations. Returns a human-readable report."""
    jdir = state_dir / "journal"
    last = jdir / "LAST"
    if not last.exists():
        return ["Nothing to undo."]
    path = jdir / last.read_text().strip()
    ops = json.loads(path.read_text()) if path.exists() else []
    report = []
    for op in reversed(ops):
        kind = op["op"]
        if kind in {"move", "trash"}:
            src, dst = Path(op["src"]), Path(op["dst"])
            if dst.exists() and not src.exists():
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dst), str(src))
                if kind == "trash":
                    Path(op.get("info", "")).unlink(missing_ok=True)
                report.append(f"restored {src}")
            else:
                report.append(f"SKIPPED {src} (changed since the task ran)")
        elif kind == "mkdir":
            p = Path(op["path"])
            try:
                p.rmdir()
                report.append(f"removed empty folder {p}")
            except OSError:
                report.append(f"kept folder {p} (not empty)")
    path.rename(path.with_suffix(".undone"))
    last.unlink()
    return report or ["Last task made no file changes."]
