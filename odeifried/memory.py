"""Local memory (one SQLite file): task history, learned skills, preferences and a small cache.

A "skill" is the exact list of actions that completed a request successfully. When the same
request comes again, the steps are replayed directly, with no AI model in the loop, so the
second time is near-instant. Every replayed step still passes the safety policy."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

# Only actions that mean the same thing when repeated later are worth remembering.
REPLAYABLE = {"open_app", "open_url", "web_search", "screenshot", "wait", "organize_by_type", "make_dir",
              "move", "batch_move", "play_music", "media_control", "set_volume", "set_theme", "run_shell",
              "press_key", "type_text"}
OBSERVE_ONLY = {"list_dir", "read_file", "file_info", "list_ui"}     # dropped from skills: they change nothing

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, ts REAL, text TEXT, route TEXT, ok INTEGER,
                                  summary TEXT, seconds REAL);
CREATE TABLE IF NOT EXISTS skills (request TEXT PRIMARY KEY, steps TEXT, uses INTEGER DEFAULT 0,
                                   learned REAL, last_used REAL);
CREATE TABLE IF NOT EXISTS prefs (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, ts REAL);
"""


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s~/.:-]", " ", text.lower())).strip()


class Memory:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    # ---- history
    def record_task(self, task_id: str, text: str, route: str, ok: bool, summary: str, seconds: float) -> None:
        self.db.execute("INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?,?,?)",
                        (task_id, time.time(), text, route, int(ok), summary, seconds))
        self.db.commit()

    def recent_tasks(self, limit: int = 10) -> list[tuple]:
        return self.db.execute("SELECT ts, text, route, ok, summary, seconds FROM tasks ORDER BY ts DESC LIMIT ?",
                               (limit,)).fetchall()

    # ---- skills
    def learn(self, request: str, steps: list[tuple[str, dict]]) -> bool:
        """Store a successful action list. Returns False if it is not safely repeatable."""
        steps = [(n, a) for n, a in steps if n not in OBSERVE_ONLY and n != "done"]
        if not steps or len(steps) > 12 or any(n not in REPLAYABLE for n, _ in steps):
            return False
        self.db.execute("INSERT INTO skills (request, steps, uses, learned, last_used) VALUES (?,?,0,?,?) "
                        "ON CONFLICT(request) DO UPDATE SET steps=excluded.steps, learned=excluded.learned",
                        (normalize(request), json.dumps(steps), time.time(), time.time()))
        self.db.commit()
        return True

    def recall(self, request: str) -> list[tuple[str, dict]] | None:
        row = self.db.execute("SELECT steps FROM skills WHERE request=?", (normalize(request),)).fetchone()
        return [(n, a) for n, a in json.loads(row[0])] if row else None

    def used(self, request: str) -> None:
        self.db.execute("UPDATE skills SET uses=uses+1, last_used=? WHERE request=?", (time.time(), normalize(request)))
        self.db.commit()

    def forget(self, request: str) -> None:
        self.db.execute("DELETE FROM skills WHERE request=?", (normalize(request),))
        self.db.commit()

    def skills(self) -> list[tuple]:
        return self.db.execute("SELECT request, uses, steps FROM skills ORDER BY last_used DESC").fetchall()

    # ---- preferences + cache
    def pref(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM prefs WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_pref(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO prefs VALUES (?,?)", (key, value))
        self.db.commit()

    def cache_get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM cache WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def cache_set(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (key, value, time.time()))
        self.db.commit()
