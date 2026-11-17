"""Detect RAM / CPU / GPU and recommend model sizes that will actually run."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class Hardware:
    ram_gb: float
    cpu_threads: int
    gpu_name: str | None
    vram_gb: float | None


def _ram_gb() -> float:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 / 1024, 1)
    except OSError:
        pass
    return 0.0


def _nvidia() -> tuple[str | None, float | None]:
    if not shutil.which("nvidia-smi"):
        return None, None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip().splitlines()[0]
        name, mem = out.rsplit(",", 1)
        return name.strip(), round(float(mem) / 1024, 1)
    except (OSError, IndexError, ValueError, subprocess.SubprocessError):
        return None, None


def detect() -> Hardware:
    name, vram = _nvidia()
    return Hardware(_ram_gb(), os.cpu_count() or 1, name, vram)


def recommend(hw: Hardware) -> dict[str, str]:
    """Pick planner/vision models by available memory. Tags are defaults; edit config to override."""
    vram = hw.vram_gb or 0
    if vram >= 24:
        return {"agent": "qwen3:32b", "vision": "qwen2.5vl:32b", "note": "Large GPU: full-size models."}
    if vram >= 12:
        return {"agent": "qwen3:14b", "vision": "qwen2.5vl:7b", "note": "12GB+ GPU: try UI-TARS-1.5-7B for vision."}
    if vram >= 8:
        return {"agent": "qwen3:8b", "vision": "qwen2.5vl:7b", "note": "8GB GPU: quantized 7B vision."}
    if hw.ram_gb >= 30:
        return {"agent": "qwen3:8b", "vision": "qwen2.5vl:7b", "note": "CPU only, lots of RAM: slow but capable."}
    return {"agent": "qwen3:4b", "vision": "qwen2.5vl:3b",
            "note": "CPU only: small models. Prefer accessibility over vision for speed."}
