"""Append-only action log: one JSON object per line in <log_dir>/actions-YYYY-MM-DD.jsonl."""
from __future__ import annotations

import json
import time
from pathlib import Path


class AuditLog:
    def __init__(self, log_dir: Path, task_id: str):
        self.task_id = task_id
        self.path = log_dir / f"actions-{time.strftime('%Y-%m-%d')}.jsonl"

    def write(self, event: str, **data) -> None:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "task": self.task_id, "event": event, **data}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
