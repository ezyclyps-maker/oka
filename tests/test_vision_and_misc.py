import struct
import zlib

import pytest

from odeifried import hardware
from odeifried.control import vision
from odeifried.safety.killswitch import KillSwitch, Stopped


@pytest.mark.parametrize("text,expected", [
    ('{"x": 120, "y": 45, "label": "Search"}', (120, 45)),
    ("click(start_box='(300,200)')", (300, 200)),
    ("<point>10 20</point>", (10, 20)),
    ("[100, 100, 200, 300]", (150, 200)),
    ("The button is at [55, 66].", (55, 66)),
])
def test_parse_point(text, expected):
    assert vision.parse_point(text) == expected


def test_parse_point_none():
    assert vision.parse_point("I cannot see it") is None


def test_to_screen_scales_and_clamps():
    assert vision.to_screen((640, 360), (1280, 720), (1920, 1080)) == (960, 540)
    assert vision.to_screen((500, 500), (1280, 720), (1920, 1080), normalized_1000=True) == (960, 540)
    assert vision.to_screen((5000, -10), (1280, 720), (1920, 1080)) == (1919, 0)


def test_png_size():
    raw = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", 800, 600, 8, 2, 0, 0, 0)
    raw += struct.pack(">I", zlib.crc32(raw[12:29]))
    assert vision.png_size(raw) == (800, 600)


def test_killswitch(tmp_path):
    ks = KillSwitch(tmp_path)
    ks.arm()
    ks.check()                                   # no flag: fine
    with pytest.raises(Stopped):                 # pressing signals the agent (here: ourselves)
        ks.press()
    with pytest.raises(Stopped):                 # and the flag keeps it stopped
        ks.check()
    ks.arm()                                     # a new task clears the old flag
    ks.check()
    ks.disarm()


def test_recommend_cpu_only_16gb():
    rec = hardware.recommend(hardware.Hardware(16, 12, None, None))
    assert rec["planner"] == "qwen3:4b"
    assert hardware.recommend(hardware.Hardware(32, 16, "RTX 3060", 12))["planner"] == "qwen3:14b"
