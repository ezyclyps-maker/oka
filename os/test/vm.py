#!/usr/bin/env python3
"""Boot the Odeifried ISO in QEMU and drive it: screenshots, key presses, typing, mouse clicks.

Used to check that an image really boots (also works without hardware acceleration, just slowly).

  vm.py start dist/odeifried-0.2.0-amd64.iso [--uefi] [--disk disk.qcow2] [--mem 4096]
  vm.py shot out.png
  vm.py key meta_l-spc          (QEMU key names, '-' joins keys pressed together)
  vm.py type "organize my downloads"
  vm.py click 640 400           (pixels on the VM screen)
  vm.py stop
"""
import json
import os
import socket
import subprocess
import sys
import time

RUN = os.environ.get("VM_RUN_DIR", "/tmp/odeifried-vm")
QMP = f"{RUN}/qmp.sock"

KEYS = {" ": "spc", "\n": "ret", ".": "dot", ",": "comma", "-": "minus", "/": "slash", ";": "semicolon",
        "'": "apostrophe", "=": "equal", "\\": "backslash", "[": "bracket_left", "]": "bracket_right", "`": "grave_accent"}
SHIFTED = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
           "_": "minus", "+": "equal", ":": "semicolon", '"': "apostrophe", "?": "slash", "<": "comma", ">": "dot",
           "~": "grave_accent", "{": "bracket_left", "}": "bracket_right", "|": "backslash"}


def qmp(*commands: dict) -> list:
    s = socket.socket(socket.AF_UNIX)
    s.connect(QMP)
    f = s.makefile("rw")
    f.readline()                                              # greeting
    out = []
    for cmd in ({"execute": "qmp_capabilities"}, *commands):
        f.write(json.dumps(cmd) + "\n")
        f.flush()
        while True:
            reply = json.loads(f.readline())
            if "return" in reply or "error" in reply:
                out.append(reply)
                break
    s.close()
    return out[1:]


def start(iso: str, uefi: bool = False, disk: str | None = None, mem: int = 4096, boot_disk: bool = False) -> None:
    os.makedirs(RUN, exist_ok=True)
    accel = "kvm" if os.path.exists("/dev/kvm") and os.access("/dev/kvm", os.W_OK) else "tcg,thread=multi"
    cmd = ["qemu-system-x86_64", "-name", "odeifried", "-machine", "q35", "-accel", accel,
           "-cpu", "host" if accel == "kvm" else "max", "-smp", "2", "-m", str(mem),
           "-vga", "std", "-display", "none", "-usb", "-device", "usb-tablet",
           "-netdev", "user,id=n0", "-device", "virtio-net-pci,netdev=n0",
           "-audiodev", "none,id=snd0", "-device", "ich9-intel-hda", "-device", "hda-output,audiodev=snd0",
           "-qmp", f"unix:{QMP},server,nowait", "-serial", f"file:{RUN}/serial.log",
           "-pidfile", f"{RUN}/pid", "-daemonize"]
    if iso and not boot_disk:
        cmd += ["-cdrom", iso, "-boot", "d"]
    if disk:
        if not os.path.exists(disk):
            subprocess.run(["qemu-img", "create", "-f", "qcow2", disk, "24G"], check=True, stdout=subprocess.DEVNULL)
        cmd += ["-drive", f"file={disk},if=virtio,format=qcow2"]
        if boot_disk:
            cmd += ["-boot", "c"]
    if uefi:
        code = next(p for p in ("/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_CODE.fd",
                                "/usr/share/ovmf/OVMF.fd") if os.path.exists(p))
        cmd += ["-drive", f"if=pflash,format=raw,readonly=on,file={code}"]
        vars_src = code.replace("CODE", "VARS")
        if os.path.exists(vars_src) and vars_src != code:
            subprocess.run(["cp", vars_src, f"{RUN}/vars.fd"], check=True)
            cmd += ["-drive", f"if=pflash,format=raw,file={RUN}/vars.fd"]
    subprocess.run(cmd, check=True)
    print(f"VM started ({accel}). Screen: vm.py shot out.png")


def shot(path: str) -> None:
    tmp = f"{RUN}/shot.ppm"
    qmp({"execute": "screendump", "arguments": {"filename": tmp}})
    time.sleep(0.3)
    subprocess.run(["convert", tmp, path], check=True)


def key(combo: str, hold_ms: int = 120) -> None:
    keys = [{"type": "qcode", "data": k} for k in combo.split("-")]
    qmp({"execute": "send-key", "arguments": {"keys": keys, "hold-time": hold_ms}})


def type_text(text: str) -> None:
    for ch in text:
        if ch in SHIFTED:
            key("shift-" + SHIFTED[ch], 40)
        elif ch.isupper():
            key("shift-" + ch.lower(), 40)
        else:
            key(KEYS.get(ch, ch), 40)
        time.sleep(0.06)


def click(x: int, y: int, w: int = 0, h: int = 0, button: str = "left") -> None:
    if not w:                                                  # read the current screen size from a screenshot
        tmp = f"{RUN}/size.ppm"
        qmp({"execute": "screendump", "arguments": {"filename": tmp}})
        time.sleep(0.2)
        with open(tmp, "rb") as f:
            f.readline()
            w, h = map(int, f.readline().split())
    ax, ay = int(x * 32767 / w), int(y * 32767 / h)
    move = [{"type": "abs", "data": {"axis": "x", "value": ax}}, {"type": "abs", "data": {"axis": "y", "value": ay}}]
    qmp({"execute": "input-send-event", "arguments": {"events": move}})
    time.sleep(0.15)
    for down in (True, False):
        qmp({"execute": "input-send-event",
             "arguments": {"events": [{"type": "btn", "data": {"down": down, "button": button}}]}})
        time.sleep(0.08)


def stop() -> None:
    try:
        qmp({"execute": "quit"})
    except OSError:
        pass


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    if a[0] == "start":
        opts = a[1:]
        get = lambda flag, default=None: opts[opts.index(flag) + 1] if flag in opts else default  # noqa: E731
        iso = next((o for o in opts if o.endswith(".iso")), "")
        start(iso, "--uefi" in opts, get("--disk"), int(get("--mem", "4096")), "--boot-disk" in opts)
    elif a[0] == "shot":
        shot(a[1])
    elif a[0] == "key":
        key(a[1])
    elif a[0] == "type":
        type_text(a[1])
    elif a[0] == "click":
        click(int(a[1]), int(a[2]), button=a[3] if len(a) > 3 else "left")
    elif a[0] == "stop":
        stop()
    else:
        sys.exit(__doc__)
