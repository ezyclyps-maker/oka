"""Load configuration: built-in defaults, overridden by ~/.config/odeifried/config.toml."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_FILE = Path(__file__).with_name("default_config.toml")
USER_FILE = Path(os.environ.get("ODEIFRIED_CONFIG", "~/.config/odeifried/config.toml")).expanduser()


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


@dataclass
class Config:
    raw: dict = field(default_factory=dict)

    def get(self, section: str, key: str, default=None):
        return self.raw.get(section, {}).get(key, default)

    @property
    def state_dir(self) -> Path:
        p = Path(self.get("paths", "state_dir", "~/.local/state/odeifried")).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def log_dir(self) -> Path:
        p = Path(self.get("paths", "log_dir", "/var/log/odeifried")).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
            probe = p / ".write-test"
            probe.touch()
            probe.unlink()
            return p
        except OSError:
            fallback = self.state_dir / "logs"
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback

    @property
    def writable_roots(self) -> list[Path]:
        return [Path(r).expanduser().resolve() for r in self.get("safety", "writable_roots", ["~"])]


def load(user_file: Path | None = None) -> Config:
    data = tomllib.loads(DEFAULT_FILE.read_text())
    uf = user_file or USER_FILE
    if uf.exists():
        data = _merge(data, tomllib.loads(uf.read_text()))
    if url := os.environ.get("ODEIFRIED_OLLAMA_URL"):
        data["models"]["ollama_url"] = url
    return Config(data)
