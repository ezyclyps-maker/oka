"""Everyday system skills: volume, media keys, power, theme, installing apps, trash, undo."""
from __future__ import annotations

import os
import re
import time

from ..journal import undo_last as _undo_last
from . import Tool, have, run

# ---------- small answers ----------
def answer(ctx, text: str) -> str:
    return text


def undo_last(ctx) -> str:
    return "Undo: " + "; ".join(_undo_last(ctx.cfg.state_dir)[:12])


# ---------- volume ----------
def _get_volume() -> int | None:
    rc, out = run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
    if rc == 0 and (m := re.search(r"([0-9.]+)", out)):
        return round(float(m.group(1)) * 100)
    rc, out = run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
    if rc == 0 and (m := re.search(r"(\d+)%", out)):
        return int(m.group(1))
    return None


def set_volume(ctx, level: int | None = None, change: int | None = None, mute: str = "") -> str:
    """level = 0..100, change = +/- percent, mute = on|off|toggle."""
    if mute:
        flag = {"on": "1", "off": "0"}.get(mute, "toggle")
        rc, out = run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", flag])
        if rc != 0:
            rc, out = run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", flag])
        return f"VERIFIED sound mute: {mute}" if rc == 0 else f"ERROR: could not change mute ({out})"
    if level is not None:
        level = max(0, min(100, int(level)))
        spec_wp, spec_pa = f"{level}%", f"{level}%"
    elif change is not None:
        change = max(-100, min(100, int(change)))
        spec_wp = f"{abs(change)}%{'+' if change >= 0 else '-'}"
        spec_pa = f"{'+' if change >= 0 else '-'}{abs(change)}%"
    else:
        return "ERROR: give a level, a change, or mute"
    rc, out = run(["wpctl", "set-volume", "--limit", "1.0", "@DEFAULT_AUDIO_SINK@", spec_wp])
    if rc != 0:
        rc, out = run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", spec_pa])
    if rc != 0:
        return f"ERROR: could not set the volume ({out})"
    now = _get_volume()
    return f"VERIFIED volume is now {now}%" if now is not None else "volume changed"


# ---------- media keys ----------
_MEDIA = {"play": "play", "resume": "play", "pause": "pause", "toggle": "play-pause",
          "stop": "stop", "next": "next", "previous": "previous"}


def media_control(ctx, action: str) -> str:
    cmd = _MEDIA.get(action.lower())
    if not cmd:
        return f"ERROR: unknown media action '{action}'"
    rc, out = run(["playerctl", cmd])
    if rc == 0:
        return f"VERIFIED media: {cmd}"
    if "No players found" in out:
        return "ERROR: nothing is playing"
    key = {"play": "XF86AudioPlay", "pause": "XF86AudioPause", "play-pause": "XF86AudioPlay",
           "stop": "XF86AudioStop", "next": "XF86AudioNext", "previous": "XF86AudioPrev"}[cmd]
    rc, out = run(["xdotool", "key", key])                       # fallback: press the media key
    return f"pressed the {cmd} media key" if rc == 0 else f"ERROR: could not control playback ({out})"


# ---------- power ----------
def _first_ok(*commands: list[str]) -> tuple[bool, str]:
    last = "no suitable command found"
    for argv in commands:
        if have(argv[0]):
            rc, out = run(argv, timeout=20)
            if rc == 0:
                return True, ""
            last = out
    return False, last


def power(ctx, action: str) -> str:
    action = action.lower()
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if action == "lock":
        ok, err = _first_ok(["xflock4"], ["loginctl", "lock-session"], ["light-locker-command", "-l"])
    elif action == "sleep":
        ok, err = _first_ok(["systemctl", "suspend"])
    elif action == "shutdown":
        ok, err = _first_ok(["systemctl", "poweroff"])
    elif action == "restart":
        ok, err = _first_ok(["systemctl", "reboot"])
    elif action == "logout":
        if "xfce" in desktop:
            ok, err = _first_ok(["xfce4-session-logout", "--logout", "--fast"])
        elif "gnome" in desktop:
            ok, err = _first_ok(["gnome-session-quit", "--logout", "--no-prompt"])
        else:
            ok, err = _first_ok(["loginctl", "terminate-session", os.environ.get("XDG_SESSION_ID", "self")])
    else:
        return f"ERROR: unknown power action '{action}'"
    return f"VERIFIED {action} requested" if ok else f"ERROR: could not {action} ({err})"


# ---------- light / dark ----------
def set_theme(ctx, mode: str) -> str:
    mode = "dark" if mode.lower().startswith("dark") else "light"
    theme = ctx.cfg.get("desktop", f"{mode}_theme", "")
    done = []
    if have("xfconf-query") and theme:
        for channel, prop in (("xsettings", "/Net/ThemeName"), ("xfwm4", "/general/theme")):
            rc, _ = run(["xfconf-query", "-c", channel, "-p", prop, "-n", "-t", "string", "-s", theme])
            done.append(rc == 0)
    if have("gsettings"):
        rc, _ = run(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme",
                     "prefer-dark" if mode == "dark" else "default"])
        done.append(rc == 0)
    return f"VERIFIED switched to {mode} mode" if any(done) else "ERROR: could not change the theme on this desktop"


# ---------- apps ----------
# Friendly names -> Flathub IDs for the apps people ask for most. Anything else is searched.
FLATHUB = {
    "spotify": "com.spotify.Client", "discord": "com.discordapp.Discord", "vlc": "org.videolan.VLC",
    "steam": "com.valvesoftware.Steam", "obs": "com.obsproject.Studio", "obs studio": "com.obsproject.Studio",
    "chrome": "com.google.Chrome", "google chrome": "com.google.Chrome", "zoom": "us.zoom.Zoom",
    "libreoffice": "org.libreoffice.LibreOffice", "office": "org.libreoffice.LibreOffice",
    "gimp": "org.gimp.GIMP", "telegram": "org.telegram.desktop", "vs code": "com.visualstudio.code",
    "vscode": "com.visualstudio.code", "visual studio code": "com.visualstudio.code",
    "minecraft": "com.mojang.Minecraft", "blender": "org.blender.Blender", "audacity": "org.audacityteam.Audacity",
    "kdenlive": "org.kde.kdenlive", "brave": "com.brave.Browser", "whatsapp": "com.rtosta.zapzap",
    "signal": "org.signal.Signal", "krita": "org.kde.krita", "inkscape": "org.inkscape.Inkscape",
}


def resolve_flatpak(name: str) -> str | None:
    key = name.lower().strip()
    if key in FLATHUB:
        return FLATHUB[key]
    if re.fullmatch(r"[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+){2,}", name.strip()):
        return name.strip()                                  # already an app ID
    rc, out = run(["flatpak", "search", "--columns=application", name], timeout=60)
    ids = [l.strip() for l in out.splitlines() if re.fullmatch(r"[\w-]+(\.[\w-]+){2,}", l.strip())]
    return ids[0] if rc == 0 and ids else None


def install_app(ctx, name: str) -> str:
    """Installs for the current user from Flathub. No admin rights needed."""
    if not have("flatpak"):
        return "ERROR: this system has no Flatpak, so I can't install apps without admin rights"
    run(["flatpak", "remote-add", "--user", "--if-not-exists", "flathub",
         "https://dl.flathub.org/repo/flathub.flatpakrepo"], timeout=60)
    app_id = resolve_flatpak(name)
    if not app_id:
        return f"ERROR: no app called '{name}' found on Flathub"
    rc, out = run(["flatpak", "install", "--user", "-y", "--noninteractive", "flathub", app_id], timeout=3600)
    if rc != 0:
        return f"ERROR: installing {app_id} failed: {out[-300:]}"
    rc, _ = run(["flatpak", "info", "--user", app_id])
    return f"VERIFIED installed {name} ({app_id})" if rc == 0 else f"installed {app_id} (could not verify)"


def empty_trash(ctx) -> str:
    rc, out = run(["gio", "trash", "--empty"], timeout=120)
    return "VERIFIED the Trash is empty" if rc == 0 else f"ERROR: could not empty the Trash ({out})"


def now(ctx=None) -> str:
    return time.strftime("It's %H:%M on %A, %B %-d, %Y.")


_S = {"type": "string"}
TOOLS = [
    Tool("answer", "Say something to the user.", {"text": _S}, ["text"], answer, llm=False),
    Tool("undo_last", "Undo the file changes of the previous task.", {}, [], undo_last, llm=False),
    Tool("set_volume", "Set the speaker volume: level 0-100, or change by +/- percent, or mute on|off|toggle.",
         {"level": {"type": "integer"}, "change": {"type": "integer"}, "mute": _S}, [], set_volume),
    Tool("media_control", "Control whatever is playing: play, pause, toggle, stop, next, previous.",
         {"action": _S}, ["action"], media_control),
    Tool("power", "lock, sleep, logout, restart or shutdown the computer.", {"action": _S}, ["action"], power, llm=False),
    Tool("set_theme", "Switch the desktop to dark or light mode.", {"mode": _S}, ["mode"], set_theme, llm=False),
    Tool("install_app", "Install an application by name (from Flathub, no admin rights needed).",
         {"name": _S}, ["name"], install_app),
    Tool("empty_trash", "Permanently delete everything in the Trash.", {}, [], empty_trash, llm=False),
]
