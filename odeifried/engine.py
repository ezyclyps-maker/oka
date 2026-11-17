"""Finds (or installs) the AI engine: the Ollama server that runs the models.

"auto" mode looks on this computer first, then on the computer hosting this virtual machine,
so Odeifried inside VirtualBox can use Ollama running on the Windows/Mac host with zero setup."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from .config import Config

LOCAL = "http://127.0.0.1:11434"
# 10.0.2.2 = the host computer as seen from a VirtualBox/QEMU "NAT" virtual machine.
# 192.168.122.1 = the host as seen from a libvirt/virt-manager virtual machine.
VM_HOSTS = ["http://10.0.2.2:11434", "http://192.168.122.1:11434"]
DOWNLOAD_URL = "https://ollama.com/download/ollama-linux-amd64.tar.zst"


def probe(url: str, timeout: float = 0.8) -> list[str] | None:
    """Model names if an Ollama server answers at url, else None."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=timeout) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception:  # noqa: BLE001 - any failure means "not here"
        return None


def _gateway_candidates() -> list[str]:
    """The default gateway (and its .1 neighbour) often is the VM host on other hypervisors."""
    try:
        out = subprocess.run(["ip", "-4", "route", "show", "default"], capture_output=True, text=True, timeout=3).stdout
        gw = out.split("via", 1)[1].split()[0]
        first = ".".join(gw.split(".")[:3] + ["1"])
        return [f"http://{h}:11434" for h in dict.fromkeys([gw, first])]
    except Exception:  # noqa: BLE001
        return []


def candidates(cfg: Config) -> list[str]:
    configured = os.environ.get("ODEIFRIED_OLLAMA_URL") or cfg.get("models", "ollama_url", "auto")
    if configured not in ("", "auto"):
        return [configured.rstrip("/")]          # an explicit address is used as-is
    return list(dict.fromkeys([LOCAL] + VM_HOSTS + _gateway_candidates()))


def find(cfg: Config, use_cache: bool = True) -> tuple[str, list[str]] | None:
    """(url, installed model names) of the first engine that answers, or None."""
    cache = cfg.state_dir / "engine.json"
    urls = candidates(cfg)
    if use_cache and cache.exists():
        try:
            last = json.loads(cache.read_text())["url"]
            if last in urls:
                urls.remove(last)
                urls.insert(0, last)
        except (ValueError, KeyError):
            pass
    for url in urls:
        models = probe(url)
        if models is not None:
            cache.write_text(json.dumps({"url": url, "ts": time.time()}))
            return url, models
    return None


def resolve_url(cfg: Config) -> str:
    """Best URL to talk to (falls back to the local default so errors mention a sensible address)."""
    found = find(cfg)
    return found[0] if found else (candidates(cfg) or [LOCAL])[0]


def is_live_session() -> bool:
    """True when running from the install USB/ISO without being installed (changes live in RAM)."""
    try:
        return Path("/run/live/medium").exists() or "boot=live" in Path("/proc/cmdline").read_text()
    except OSError:
        return False


def local_dir() -> Path:
    return Path.home() / ".local/share/odeifried/ollama"


def has_nvidia() -> bool:
    return bool(shutil.which("nvidia-smi")) or Path("/proc/driver/nvidia").exists()


UNIT = """[Unit]
Description=Odeifried AI engine (Ollama)

[Service]
ExecStart={bin} serve
Environment=OLLAMA_HOST=127.0.0.1:11434
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""


def install_local(progress=lambda text: None) -> str:
    """Install Ollama for the current user only (no admin rights) and start it. Returns its URL."""
    if not shutil.which("zstd"):
        raise RuntimeError("the 'zstd' tool is missing (sudo apt install zstd)")
    dest = local_dir()
    dest.mkdir(parents=True, exist_ok=True)
    progress("Downloading the AI engine (about 1.8 GB)...")
    # Stream download -> unpack, skipping NVIDIA libraries when there is no NVIDIA card.
    tar = ["tar", "--zstd", "-x", "-C", str(dest)]
    if not has_nvidia():
        tar += ["--exclude=lib/ollama/cuda*", "--exclude=lib/ollama/mlx*"]
    curl = subprocess.Popen(["curl", "-fL", "--retry", "3", "-s", DOWNLOAD_URL], stdout=subprocess.PIPE)
    unpack = subprocess.run(tar, stdin=curl.stdout)
    if curl.wait() != 0 or unpack.returncode != 0:
        raise RuntimeError("download failed - check the internet connection and try again")
    binary = dest / "bin/ollama"
    if not binary.exists():
        raise RuntimeError("the download did not contain the engine")
    progress("Starting the AI engine...")
    unit = Path.home() / ".config/systemd/user/odeifried-ollama.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(UNIT.format(bin=binary))
    started = False
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        started = subprocess.run(["systemctl", "--user", "enable", "--now", "odeifried-ollama.service"],
                                 capture_output=True).returncode == 0
    if not started:  # no systemd user session (e.g. containers): run it directly
        subprocess.Popen([str(binary), "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, env={**os.environ, "OLLAMA_HOST": "127.0.0.1:11434"})
    for _ in range(40):
        if probe(LOCAL, 0.5) is not None:
            return LOCAL
        time.sleep(0.5)
    raise RuntimeError("the AI engine was installed but did not start")
