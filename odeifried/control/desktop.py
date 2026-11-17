"""GUI tools for the agent (X11 session).
Order of preference: launch directly -> accessibility tree (AT-SPI) -> vision model + xdotool."""
from __future__ import annotations

import configparser
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from ..tools import Tool, launch_detached
from . import vision

APP_DIRS = [Path("/usr/share/applications"), Path.home() / ".local/share/applications",
            Path("/var/lib/snapd/desktop/applications"), Path("/var/lib/flatpak/exports/share/applications")]
# Everyday names -> the programs that provide them (first one installed wins).
ALIASES = {
    "files": ["thunar", "nautilus", "nemo", "dolphin"], "file manager": ["thunar", "nautilus", "nemo", "dolphin"],
    "file explorer": ["thunar", "nautilus", "nemo", "dolphin"], "explorer": ["thunar", "nautilus", "nemo", "dolphin"],
    "browser": ["firefox-esr", "firefox", "chromium", "google-chrome"], "web browser": ["firefox-esr", "firefox", "chromium"],
    "firefox": ["firefox-esr", "firefox"], "internet": ["firefox-esr", "firefox"],
    "terminal": ["xfce4-terminal", "gnome-terminal", "konsole", "xterm"], "command prompt": ["xfce4-terminal", "gnome-terminal"],
    "settings": ["xfce-settings-manager", "xfce4-settings-manager", "gnome-control-center", "systemsettings"],
    "control panel": ["xfce-settings-manager", "xfce4-settings-manager", "gnome-control-center"],
    "text editor": ["mousepad", "gnome-text-editor", "gedit", "kate"], "notepad": ["mousepad", "gnome-text-editor", "gedit"],
    "calculator": ["galculator", "gnome-calculator", "kcalc"], "task manager": ["xfce4-taskmanager", "gnome-system-monitor"],
    "image viewer": ["ristretto", "eog"], "photos": ["ristretto", "eog"], "video player": ["mpv", "vlc", "totem"],
    "spotify": ["spotify", "com.spotify.Client", "spotify_spotify"], "discord": ["discord", "com.discordapp.Discord"],
    "store": ["gnome-software", "org.gnome.Software"], "volume mixer": ["pavucontrol"],
}


def _xdotool(*args: str) -> None:
    if not shutil.which("xdotool"):
        raise RuntimeError("xdotool missing: sudo apt install xdotool")
    subprocess.run(["xdotool", *args], check=True, timeout=20)


def _entries() -> list[tuple[str, str]]:
    """(desktop file id, shown name) of every installed app that appears in menus."""
    out = []
    dirs = APP_DIRS + [Path.home() / ".local/share/flatpak/exports/share/applications"]
    for d in dirs:
        for f in sorted(d.glob("*.desktop")) if d.is_dir() else []:
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                cp.read(f, encoding="utf-8")
                if cp.get("Desktop Entry", "NoDisplay", fallback="false").lower() == "true" and "settings" not in f.stem:
                    continue
                out.append((f.stem, cp.get("Desktop Entry", "Name", fallback="").lower()))
            except (configparser.Error, UnicodeDecodeError):
                continue
    return out


def find_desktop_entry(name: str) -> str | None:
    """Match an app by .desktop file id or its Name= field. Only installed apps can be launched."""
    want = name.lower().strip()
    if not want or not re.fullmatch(r"[\w .+&'-]+", want):
        return None
    entries = _entries()
    for candidate in ALIASES.get(want, []) + [want]:
        c = candidate.lower()
        for stem, shown in entries:
            low = stem.lower()
            if c in {low, shown} or low.endswith("_" + c) or low.endswith("." + c) or low.endswith("-" + c):
                return stem
    return None


# ---------- direct ----------
def open_app(ctx, name: str) -> str:
    entry = find_desktop_entry(name)
    if not entry:
        return f"ERROR: no installed app called '{name}'"
    launch_detached(["gtk-launch", entry])
    time.sleep(2)
    return f"VERIFIED opened {name} ({entry})"


def open_url(ctx, url: str) -> str:
    if urlparse(url).scheme not in {"http", "https"}:
        return "ERROR: only http(s) URLs"
    launch_detached(["xdg-open", url])
    time.sleep(1)
    return f"opened {url} in the browser"


def web_search(ctx, query: str) -> str:
    return open_url(ctx, "https://duckduckgo.com/?q=" + quote_plus(query))


def screenshot(ctx, name: str = "") -> str:
    safe = re.sub(r"[^\w.-]", "_", name)[:60] or f"screenshot-{vision.stamp()}"
    path = Path.home() / "Pictures" / "Odeifried" / (safe if safe.endswith(".png") else safe + ".png")
    vision.capture(path)
    return f"VERIFIED screenshot saved: {path}"


def wait(ctx, seconds: float = 2) -> str:
    time.sleep(min(float(seconds), 15))
    return "waited"


def press_key(ctx, key: str) -> str:
    _xdotool("key", "--clearmodifiers", key)
    return f"pressed {key}"


def type_text(ctx, text: str, element_id: int | None = None) -> str:
    if element_id is not None:
        acc = ctx.ui_cache.get(int(element_id))
        if acc is None:
            return "ERROR: unknown element id; call list_ui again"
        try:
            et = acc.get_editable_text_iface()
            if et:
                et.set_text_contents(text)
                return f"typed into element {element_id}"
        except Exception:  # noqa: BLE001 - fall through to keyboard typing
            pass
        _click_acc(acc)
    _xdotool("type", "--delay", "20", "--", text)
    return "typed text"


# ---------- accessibility tree ----------
_INTERACTIVE = {"push button", "toggle button", "check box", "radio button", "menu item", "link", "entry",
                "text", "combo box", "page tab", "list item", "icon", "spin button", "slider", "password text"}


def _atspi():
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
    return Atspi


def _active_window(Atspi):
    desk = Atspi.get_desktop(0)
    for i in range(desk.get_child_count()):
        app = desk.get_child_at_index(i)
        for j in range(app.get_child_count() if app else 0):
            win = app.get_child_at_index(j)
            if win and win.get_state_set().contains(Atspi.StateType.ACTIVE):
                return app, win
    return None, None


def list_ui(ctx, max_items: int = 60) -> str:
    """Numbered list of the focused window's interactive elements, read from the accessibility tree."""
    Atspi = _atspi()
    app, win = _active_window(Atspi)
    if not win:
        return "ERROR: no focused window found"
    ctx.ui_cache.clear()
    lines, stack, seen = [], [win], 0
    while stack and len(lines) < max_items and seen < 4000:
        node = stack.pop()
        seen += 1
        try:
            role, name = node.get_role_name(), (node.get_name() or "").strip()
            states = node.get_state_set()
            if role in _INTERACTIVE and states.contains(Atspi.StateType.SHOWING) and (name or role in {"entry", "text"}):
                idx = len(ctx.ui_cache) + 1
                ctx.ui_cache[idx] = node
                lines.append(f"[{idx}] {role}: {name[:80] or '(unnamed)'}")
            stack.extend(node.get_child_at_index(k) for k in reversed(range(min(node.get_child_count(), 300))))
        except Exception:  # noqa: BLE001 - dead nodes are common
            continue
    head = f"window '{win.get_name()}' of {app.get_name()}"
    if not lines:
        return head + ": no accessible elements. Use describe_and_click (vision) instead."
    return head + "\n" + "\n".join(lines)


def _click_acc(acc) -> None:
    try:
        action = acc.get_action_iface()
        if action:
            for i in range(action.get_n_actions()):
                if action.get_action_name(i) in {"click", "press", "activate", "jump"}:
                    action.do_action(i)
                    return
    except Exception:  # noqa: BLE001
        pass
    Atspi = _atspi()
    ext = acc.get_extents(Atspi.CoordType.SCREEN)
    _xdotool("mousemove", str(ext.x + ext.width // 2), str(ext.y + ext.height // 2), "click", "1")


def click_element(ctx, element_id: int, name: str = "") -> str:
    acc = ctx.ui_cache.get(int(element_id))
    if acc is None:
        return "ERROR: unknown element id; call list_ui first"
    _click_acc(acc)
    time.sleep(1)
    return f"clicked [{element_id}] {acc.get_name()}"


# ---------- vision fallback ----------
def _screen_size() -> tuple[int, int]:
    out = subprocess.run(["xdotool", "getdisplaygeometry"], capture_output=True, text=True, timeout=10).stdout.split()
    return int(out[0]), int(out[1])


def describe_and_click(ctx, description: str) -> str:
    """Screenshot -> vision model finds the element -> xdotool clicks it."""
    shot = ctx.cfg.state_dir / "vision.png"
    vision.capture(shot)
    png = vision.shrink(shot, int(ctx.cfg.get("agent", "screenshot_max_width", 1280)))
    pt, label = vision.locate(ctx.llm, ctx.cfg.get("models", "vision"), description, png)
    if not pt:
        return f"NOT FOUND on screen: {description}"
    x, y = vision.to_screen(pt, vision.png_size(png), _screen_size())
    _xdotool("mousemove", str(x), str(y), "click", "1")
    time.sleep(1)
    return f"clicked at ({x},{y}) on '{label or description}'"


_S = {"type": "string"}
_I = {"type": "integer"}
TOOLS = [
    Tool("open_app", "Launch an installed application by name (e.g. firefox, files, terminal, settings).",
         {"name": _S}, ["name"], open_app),
    Tool("open_url", "Open a web page in the default browser.", {"url": _S}, ["url"], open_url),
    Tool("web_search", "Search the web in the browser (fastest way to look something up).",
         {"query": _S}, ["query"], web_search),
    Tool("screenshot", "Save a screenshot of the whole screen to ~/Pictures/Odeifried/.", {"name": _S}, [], screenshot),
    Tool("wait", "Wait for something to load.", {"seconds": {"type": "number"}}, [], wait),
    Tool("list_ui", "List the buttons, fields and links of the focused window, numbered.", {}, [], list_ui),
    Tool("click_element", "Click an element by its number from list_ui. Also pass its name.",
         {"element_id": _I, "name": _S}, ["element_id", "name"], click_element),
    Tool("type_text", "Type text, optionally into an element number from list_ui.",
         {"text": _S, "element_id": _I}, ["text"], type_text),
    Tool("press_key", "Press a key or combo, xdotool style: Return, Escape, ctrl+l, ctrl+t.", {"key": _S}, ["key"], press_key),
    Tool("describe_and_click", "LAST RESORT (slow): find an element visually by description and click it.",
         {"description": _S}, ["description"], describe_and_click),
]
