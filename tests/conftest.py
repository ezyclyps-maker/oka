import pytest

from odeifried.config import Config, load


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Config pointing logs/state into a temp dir, with $HOME = tmp home."""
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ODEIFRIED_CONFIG", str(tmp_path / "none.toml"))
    c = load(tmp_path / "none.toml")
    c.raw["paths"]["log_dir"] = str(tmp_path / "logs")
    c.raw["paths"]["state_dir"] = str(tmp_path / "state")
    c.raw["safety"]["writable_roots"] = [str(home)]
    return c
