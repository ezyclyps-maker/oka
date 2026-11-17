"""Instant commands.

Everyday requests ("organize my downloads", "play lil baby on spotify", "volume 40") are recognized
here and turned straight into actions. No AI model is involved, so they run in a fraction of a second
even on a slow computer. Anything not recognized is handed to the AI agent.

The actions still go through the same safety policy, log and undo journal as everything else."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote_plus


@dataclass
class Plan:
    intent: str
    steps: list[tuple[str, dict]]


# ---------- tidy up what the user typed ----------
_PREFIX = re.compile(
    r"^(?:(?:hey|hi|ok|okay|yo)\s+odeifried[,!\s]*|odeifried[,!\s]+|please\s+|pls\s+|can you\s+|could you\s+|"
    r"would you\s+|will you\s+|i want you to\s+|i need you to\s+|i'd like you to\s+|go ahead and\s+|just\s+)+", re.I)
_SUFFIX = re.compile(r"(?:[,\s]+(?:please|pls|for me|right now|now|thanks|thank you))+$", re.I)


def clean(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip().strip(".!").strip()
    t = _SUFFIX.sub("", _PREFIX.sub("", t)).strip().strip(".!,").strip()
    return t


# ---------- folders and web sites people call by name ----------
_XDG = {"downloads": "DOWNLOAD", "download": "DOWNLOAD", "desktop": "DESKTOP", "documents": "DOCUMENTS",
        "docs": "DOCUMENTS", "pictures": "PICTURES", "photos": "PICTURES", "music": "MUSIC", "videos": "VIDEOS"}
_FALLBACK = {"DOWNLOAD": "Downloads", "DESKTOP": "Desktop", "DOCUMENTS": "Documents", "PICTURES": "Pictures",
             "MUSIC": "Music", "VIDEOS": "Videos"}


def folder_path(word: str) -> str:
    key = _XDG[word.lower().replace(" folder", "").strip()]
    try:
        out = subprocess.run(["xdg-user-dir", key], capture_output=True, text=True, timeout=3).stdout.strip()
        if out and Path(out) != Path.home():
            return out
    except (OSError, subprocess.SubprocessError):
        pass
    return str(Path.home() / _FALLBACK[key])


CLASSROOM_MISSING = "https://classroom.google.com/u/0/a/missing/all"
SITES = {
    "youtube": "https://www.youtube.com", "gmail": "https://mail.google.com", "google": "https://www.google.com",
    "google classroom": "https://classroom.google.com", "classroom": "https://classroom.google.com",
    "google docs": "https://docs.google.com", "google drive": "https://drive.google.com",
    "drive": "https://drive.google.com", "google slides": "https://slides.google.com",
    "google sheets": "https://sheets.google.com", "google calendar": "https://calendar.google.com",
    "calendar": "https://calendar.google.com", "google maps": "https://maps.google.com",
    "maps": "https://maps.google.com", "google translate": "https://translate.google.com",
    "netflix": "https://www.netflix.com", "twitch": "https://www.twitch.tv", "reddit": "https://www.reddit.com",
    "instagram": "https://www.instagram.com", "tiktok": "https://www.tiktok.com", "twitter": "https://x.com",
    "facebook": "https://www.facebook.com", "whatsapp": "https://web.whatsapp.com",
    "discord": "https://discord.com/app", "spotify": "https://open.spotify.com", "github": "https://github.com",
    "amazon": "https://www.amazon.com", "wikipedia": "https://www.wikipedia.org", "outlook": "https://outlook.live.com",
}

HELP = ("Tell me what to do in plain words. Things I do instantly: organize my downloads · play <artist> on "
        "spotify · open <app or site> · search for <anything> · volume 40 · pause / next song · take a screenshot · "
        "dark mode · install <app> · lock · undo. Chain them with \"then\". Everything else I work out with the AI model.")

HOMEWORK_NOTE = ("I opened your missing work in Google Classroom. I can't do and hand in assignments by myself: "
                 "open one and ask me to explain it or help you draft an answer, then check it and turn it in yourself.")

_DOMAIN = re.compile(r"^(?:https?://)?[\w-]+(?:\.[\w-]+)+(?:/\S*)?$")
_F = r"(?:files|stuff|downloads?|desktop|documents|docs|pictures|photos|videos|music)"


def _organize(m: re.Match) -> list[tuple[str, dict]]:
    what = m.group("what").lower()
    if what.startswith(("~", "/")):
        paths = [what if not m.group("what").startswith(("~", "/")) else m.group("what")]
    elif what in {"files", "stuff"}:
        paths = [folder_path("downloads"), folder_path("desktop")]
    else:
        paths = [folder_path(what)]
    return [("organize_by_type", {"path": p}) for p in paths]


def _open(m: re.Match, app_exists: Callable[[str], bool]) -> list[tuple[str, dict]] | None:
    x = m.group("x").strip()
    x = re.sub(r"^(?:the|my|up)\s+", "", x, flags=re.I)
    x = re.sub(r"\s+(?:app|application|program|website|site|page)$", "", x, flags=re.I)
    low = x.lower()
    if low in _XDG or low.replace(" folder", "") in _XDG:
        return [("run_shell", {"command": f"xdg-open '{folder_path(low)}'"})]
    if app_exists(low):
        return [("open_app", {"name": low})]
    if low in SITES:
        return [("open_url", {"url": SITES[low]})]
    if _DOMAIN.match(x) and " " not in x:
        return [("open_url", {"url": x if x.startswith("http") else "https://" + x})]
    return None


def _play(m: re.Match, app_exists: Callable[[str], bool]) -> list[tuple[str, dict]] | None:
    q = (m.group("q") or "").strip()
    svc = (m.group("svc") or "").lower().replace("yt", "youtube").replace("youtube music", "youtube")
    q = re.sub(r"^(?:me\s+)?(?:some\s+|a\s+)?", "", q, flags=re.I)
    q = re.sub(r"^(?:songs?|music|tracks?|something|anything)\s+(?:by|from)\s+", "", q, flags=re.I)
    q = re.sub(r"^the artist\s+", "", q, flags=re.I)
    if q.lower() in {"", "music", "song", "songs", "something", "it", "the music"}:
        return [("media_control", {"action": "play"})]
    if not svc and app_exists(q.lower()):                 # "play minecraft" means the game, not a song
        return [("open_app", {"name": q.lower()})]
    return [("play_music", {"query": q, **({"service": svc} if svc else {})})]


def match(text: str, app_exists: Callable[[str], bool] | None = None) -> Plan | None:
    """Return the actions for an instantly-recognized request, else None."""
    if app_exists is None:
        from .control.desktop import find_desktop_entry
        app_exists = lambda n: find_desktop_entry(n) is not None  # noqa: E731
    t = clean(text).rstrip("?").strip()
    low = t.lower()

    def rx(pattern: str) -> re.Match | None:
        return re.fullmatch(pattern, t, flags=re.I)

    # --- files
    if m := rx(rf"(?:organi[sz]e|sort(?: out)?|clean up|tidy(?: up)?|declutter)\s+(?:all\s+)?(?:of\s+)?(?:my\s+|the\s+)?"
               rf"(?P<what>{_F}|[~/]\S*)(?:\s+folder)?(?:\s+(?:by|into|according to)\s+(?:file\s*)?(?:types?|kind))?"):
        return Plan("organize", _organize(m))
    if rx(r"empty\s+(?:the\s+|my\s+)?(?:trash|recycle bin|rubbish|bin)"):
        return Plan("empty_trash", [("empty_trash", {})])
    if rx(r"undo(?:\s+(?:that|it|this|the last (?:task|thing|action)|last (?:task|thing|action)))?"):
        return Plan("undo", [("undo_last", {})])

    # --- school
    if rx(r"(?:show|open|list|check|see|view|what are|what's|whats)(?:\s+me)?(?:\s+all)?(?:\s+of)?(?:\s+my)?\s+"
          r"(?:missing|late|overdue|unfinished|incomplete)\s+(?:assignments?|work|homework)"
          r"(?:\s+(?:in|on|from|for)\s+(?:google\s+)?classroom)?"):
        return Plan("classroom_missing", [("open_url", {"url": CLASSROOM_MISSING})])
    if rx(r"(?:complete|do|finish|submit|turn in|hand in)(?:\s+all)?(?:\s+of)?(?:\s+my)?\s+(?:google\s+)?(?:classroom'?s?\s+)?"
          r"(?:missing\s+|late\s+|overdue\s+)?(?:assignments?|homework|work)(?:\s+(?:in|on|from|for)\s+(?:google\s+)?classroom)?"):
        return Plan("classroom_homework", [("open_url", {"url": CLASSROOM_MISSING}), ("answer", {"text": HOMEWORK_NOTE})])

    # --- music and sound
    if m := rx(r"play(?:\s+(?P<q>.+?))??(?:\s+(?:on|in|with|using|from)\s+(?P<svc>spotify|youtube music|youtube|yt))?"):
        if plan := _play(m, app_exists):
            return Plan("play", plan)
    if m := rx(r"(?P<a>pause|resume|unpause)(?:\s+(?:the\s+|my\s+)?(?:music|song|track|video|playback|spotify))?"):
        return Plan("media", [("media_control", {"action": "pause" if m.group("a").lower() == "pause" else "play"})])
    if rx(r"stop\s+(?:the\s+|my\s+)?(?:music|song|track|playback|spotify)"):
        return Plan("media", [("media_control", {"action": "pause"})])
    if rx(r"(?:skip|next)(?:\s+(?:this\s+|the\s+)?(?:song|track))?|(?:play\s+|go to\s+)?(?:the\s+)?next\s+(?:song|track)"):
        return Plan("media", [("media_control", {"action": "next"})])
    if rx(r"(?:previous|prev|last)\s+(?:song|track)|(?:play\s+|go (?:back )?to\s+)?(?:the\s+)?previous\s+(?:song|track)|go back a (?:song|track)"):
        return Plan("media", [("media_control", {"action": "previous"})])
    if m := rx(r"(?:(?:set|change|turn|put)\s+)?(?:the\s+)?(?:volume|sound)\s+(?:to\s+|at\s+)?(?P<n>\d{1,3})\s*(?:%|percent)?"):
        return Plan("volume", [("set_volume", {"level": min(100, int(m.group("n")))})])
    if m := rx(r"(?:(?:turn|put)\s+)?(?:the\s+)?(?:volume|sound)\s+(?P<d>up|down)|turn\s+(?:it|the (?:volume|sound|music))\s+(?P<d2>up|down)"
               r"|(?:make\s+(?:it|the (?:volume|sound|music))\s+)?(?P<d3>louder|quieter)"):
        d = (m.group("d") or m.group("d2") or m.group("d3")).lower()
        return Plan("volume", [("set_volume", {"change": 10 if d in {"up", "louder"} else -10})])
    if m := rx(r"(?P<m>mute|unmute)(?:\s+(?:the\s+|my\s+)?(?:sound|volume|audio|computer|pc|laptop|speakers?))?"):
        return Plan("volume", [("set_volume", {"mute": "on" if m.group("m").lower() == "mute" else "off"})])

    # --- look and feel
    if m := rx(r"(?:(?:turn on|enable|switch to|use|set|go)\s+)?(?:the\s+)?(?P<m>dark|light)\s+(?:mode|theme)(?:\s+on)?"):
        return Plan("theme", [("set_theme", {"mode": m.group("m").lower()})])
    if rx(r"(?:turn off|disable)\s+(?:the\s+)?dark\s+(?:mode|theme)"):
        return Plan("theme", [("set_theme", {"mode": "light"})])

    # --- screenshots, time, help
    if rx(r"(?:take\s+|grab\s+|capture\s+)?(?:a\s+)?(?:screenshot|screen shot|screen capture)(?:\s+of\s+(?:the\s+|my\s+)?(?:screen|desktop))?"):
        return Plan("screenshot", [("screenshot", {})])
    if rx(r"(?:what(?:'s| is)\s+the\s+time|what\s+time\s+is\s+it|what(?:'s| is)\s+(?:the\s+|today'?s\s+)date|what\s+day\s+is\s+it(?:\s+today)?)(?:\s+(?:right\s+)?now)?"):
        from .tools.system import now
        return Plan("time", [("answer", {"text": now()})])
    if low in {"help", "what can you do", "what can i say", "what can i ask", "commands"}:
        return Plan("help", [("answer", {"text": HELP})])

    # --- power
    if rx(r"lock(?:\s+(?:the|my)\s+(?:screen|computer|pc|laptop))?"):
        return Plan("power", [("power", {"action": "lock"})])
    if rx(r"sleep|suspend|go to sleep|put\s+(?:the\s+|my\s+)?(?:computer|pc|laptop)\s+to\s+sleep"):
        return Plan("power", [("power", {"action": "sleep"})])
    if rx(r"(?:shut\s?down|power off|turn off)(?:\s+(?:the\s+|my\s+)?(?:computer|pc|laptop|system))?"):
        return Plan("power", [("power", {"action": "shutdown"})])
    if rx(r"(?:restart|reboot)(?:\s+(?:the\s+|my\s+)?(?:computer|pc|laptop|system))?"):
        return Plan("power", [("power", {"action": "restart"})])
    if rx(r"log\s?out|sign\s?out|log off"):
        return Plan("power", [("power", {"action": "logout"})])

    # --- apps and the web
    if m := rx(r"install\s+(?:the\s+)?(?P<x>.+?)(?:\s+app)?"):
        return Plan("install", [("install_app", {"name": m.group("x").strip()})])
    if m := rx(r"(?:search|google|look up|search up)(?:\s+(?:the\s+)?(?:web|internet|online|google))?(?:\s+for)?\s+(?P<q>.+)"):
        return Plan("search", [("web_search", {"query": m.group("q").strip()})])
    if m := rx(r"(?:what(?:'s| is)\s+the\s+)?weather(?:\s+like)?(?:\s+(?:in|for|at)\s+(?P<p>.+?))?(?:\s+(?:today|tomorrow|this week))?"):
        return Plan("search", [("web_search", {"query": "weather " + (m.group("p") or "today")})])
    if m := rx(r"(?:open|launch|start|run|go to|bring up|pull up)\s+(?P<x>.+)"):
        if steps := _open(m, app_exists):
            return Plan("open", steps)
    return None


# ---------- "do this, then that" ----------
_STRONG = re.compile(
    r"(?:\s*[,;]\s*|\s+)(?:and\s+)?(?:then|after that|afterwards|finally)\b,?\s*|\s*;\s*|\n+|(?<=[a-z0-9])\.\s+(?=[A-Za-z])",
    re.I)
_WEAK = re.compile(r"\s*,\s*(?:and\s+)?|\s+and\s+", re.I)


def split_tasks(text: str, is_instant: Callable[[str], bool] | None = None) -> list[str]:
    """Split a chained request into single tasks.

    "then / after that / ;" always split. A plain "and" or comma only splits when every piece is an
    instant command on its own ("pause and lock" -> 2 tasks, "search for cats and dogs" -> 1 task)."""
    if is_instant is None:
        is_instant = lambda s: match(s) is not None  # noqa: E731
    out: list[str] = []
    for part in _STRONG.split(text):
        part = clean(part or "")
        if not part:
            continue
        pieces = [clean(p) for p in _WEAK.split(part) if clean(p)]
        if len(pieces) < 2 or is_instant(part):
            out.append(part)
            continue
        fixed, verb = [], None
        for piece in pieces:
            if is_instant(piece):
                fixed.append(piece)
                verb = piece.split()[0]
            elif verb and is_instant(f"{verb} {piece}"):       # "open firefox and files" -> "open files"
                fixed.append(f"{verb} {piece}")
            else:
                fixed = []
                break
        out += fixed or [part]
    return out


def describe(plan: Plan) -> str:
    return ", ".join(name for name, _ in plan.steps)


def web_query_url(query: str) -> str:
    return "https://duckduckgo.com/?q=" + quote_plus(query)
