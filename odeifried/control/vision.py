"""Vision fallback helpers: screenshots, image size, and parsing click coordinates from model output.
Kept free of GUI imports so it can be unit-tested anywhere."""
from __future__ import annotations

import base64
import json
import re
import shutil
import struct
import subprocess
import time
from pathlib import Path


def png_size(data: bytes) -> tuple[int, int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    return struct.unpack(">II", data[16:24])


def capture(path: Path) -> Path:
    """Full-screen screenshot, trying common X11 tools in order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for cmd in (["gnome-screenshot", "-f", str(path)], ["scrot", "-o", str(path)],
                ["import", "-window", "root", str(path)]):
        if shutil.which(cmd[0]) and subprocess.run(cmd, capture_output=True, timeout=20).returncode == 0 and path.exists():
            return path
    raise RuntimeError("no screenshot tool worked (install scrot: sudo apt install scrot)")


def shrink(src: Path, max_width: int) -> bytes:
    """Downscale for the vision model (big speed win on CPU). Needs ImageMagick; otherwise sends full size."""
    if shutil.which("convert"):
        r = subprocess.run(["convert", str(src), "-resize", f"{max_width}x>", "png:-"], capture_output=True, timeout=30)
        if r.returncode == 0:
            return r.stdout
    return src.read_bytes()


_PATTERNS = [
    re.compile(r'"x"\s*:\s*(-?\d+(?:\.\d+)?)\s*,\s*"y"\s*:\s*(-?\d+(?:\.\d+)?)'),   # {"x": 10, "y": 20}
    re.compile(r"<point>\s*(\d+(?:\.\d+)?)[\s,]+(\d+(?:\.\d+)?)\s*</point>"),        # <point>10 20</point>
    re.compile(r"\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\)"),                   # (10,20) / start_box='(10,20)'
    re.compile(r"\[\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*(?:,\s*\d+(?:\.\d+)?\s*,\s*\d+(?:\.\d+)?\s*)?\]"),  # [x,y] or [x1,y1,x2,y2]
]


def parse_point(text: str) -> tuple[float, float] | None:
    """Extract one (x, y) from the model's reply. For a box [x1,y1,x2,y2] returns its centre."""
    box = re.search(r"\[\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\]", text)
    if box:
        x1, y1, x2, y2 = map(float, box.groups())
        return (x1 + x2) / 2, (y1 + y2) / 2
    for pat in _PATTERNS:
        m = pat.search(text)
        if m:
            return float(m.group(1)), float(m.group(2))
    return None


def to_screen(pt: tuple[float, float], sent: tuple[int, int], screen: tuple[int, int],
              normalized_1000: bool = False) -> tuple[int, int]:
    """Map a point in the image we sent back to real screen pixels."""
    x, y = pt
    if normalized_1000:                 # some models (e.g. UI-TARS-72B) answer on a 0..1000 grid
        x, y = x / 1000 * sent[0], y / 1000 * sent[1]
    sx, sy = screen[0] / sent[0], screen[1] / sent[1]
    return (min(max(int(round(x * sx)), 0), screen[0] - 1), min(max(int(round(y * sy)), 0), screen[1] - 1))


PROMPT = ("You are a GUI grounding model. Find this element on the screenshot: \"{target}\".\n"
          "Reply with ONLY JSON: {{\"x\": <pixel x>, \"y\": <pixel y>, \"label\": \"<visible text of the element>\"}}. "
          "If it is not visible reply {{\"x\": -1, \"y\": -1, \"label\": \"\"}}.")


def locate(llm, model: str, target: str, png: bytes) -> tuple[tuple[float, float] | None, str]:
    reply = llm.chat(model, [{"role": "user", "content": PROMPT.format(target=target),
                              "images": [base64.b64encode(png).decode()]}]).get("content", "")
    label = ""
    try:
        label = json.loads(re.search(r"\{.*\}", reply, re.S).group(0)).get("label", "")
    except (AttributeError, ValueError):
        pass
    pt = parse_point(reply)
    if pt and pt[0] < 0:
        return None, label
    return pt, label


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")
