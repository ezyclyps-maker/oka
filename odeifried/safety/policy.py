"""Permission tiers. Every action the agent wants to take is classified here BEFORE it runs.

SAFE  -> runs immediately (read, list, open apps, reversible moves inside your home folder)
ASK   -> shows a confirmation with the exact action; runs only if you say yes
NEVER -> refused, always (disk wipes, privilege escalation, disabling security)

Rule of thumb: when unsure, the policy answers ASK. Unknown tools answer NEVER.
"""
from __future__ import annotations

import enum
import re
import shlex
from dataclasses import dataclass
from pathlib import Path


class Tier(enum.IntEnum):
    SAFE = 0
    ASK = 1
    NEVER = 2


@dataclass(frozen=True)
class Decision:
    tier: Tier
    reason: str


SAFE = lambda r: Decision(Tier.SAFE, r)    # noqa: E731
ASK = lambda r: Decision(Tier.ASK, r)      # noqa: E731
NEVER = lambda r: Decision(Tier.NEVER, r)  # noqa: E731


def _worst(*ds: Decision) -> Decision:
    return max(ds, key=lambda d: d.tier)


# ---------- shell ----------
_NEVER_SHELL = [
    (r"\b(sudo|su|pkexec|doas|run0)\b", "privilege escalation goes through the approval daemon, never the agent"),
    (r"\bmkfs(\.\w+)?\b|\bwipefs\b|\b(fdisk|sfdisk|gdisk|sgdisk|parted|cfdisk)\b", "disk formatting/partitioning"),
    (r"\bdd\b.*\bof=/dev/", "raw write to a disk device"),
    (r">\s*/dev/(sd|nvme|vd|hd|mmcblk|disk)", "redirect onto a disk device"),
    (r"\bshred\b.*\s/dev/", "shredding a device"),
    (r"\brm\b(?=.*\s-\w*[rR])(?=.*\s(/|/\*|~|~/|~/\*|\$HOME/?|\$HOME/\*|/(bin|boot|dev|etc|home|lib\w*|opt|proc|root|sbin|srv|sys|usr|var)/?)(\s|$|;|&|\|))",
     "recursive delete of the root, home, or a system directory"),
    (r"--no-preserve-root", "rm --no-preserve-root"),
    (r"\b(ufw\s+disable|setenforce\s+0|aa-teardown)\b", "disabling security"),
    (r"\bsystemctl\s+(stop|disable|mask)\s+(ufw|firewalld|apparmor|auditd|odeifried\S*)", "disabling security or Odeifried itself"),
    (r":\(\)\s*\{.*\};\s*:", "fork bomb"),
    (r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z|da)?sh\b", "piping a download straight into a shell"),
    (r"\bchmod\s+(-\w+\s+)*[0-7]*777\s+/(\s|$)|\bchown\s+-R\s+\S+\s+/(\s|$)", "changing permissions of the whole system"),
    (r"/var/log/odeifried", "tampering with the Odeifried audit log"),
]

_ASK_PROGRAMS = {
    "rm", "rmdir", "mv", "cp", "ln", "chmod", "chown", "truncate", "shred", "kill", "pkill", "killall",
    "apt", "apt-get", "dpkg", "snap", "flatpak", "pip", "pip3", "npm", "gsettings", "dconf", "nmcli",
    "systemctl", "crontab", "reboot", "shutdown", "poweroff", "git", "ssh", "scp", "rsync", "curl", "wget",
    "mail", "sendmail", "tee", "sed", "touch", "mkdir",
}

_SEGMENT_SPLIT = re.compile(r"\|\||&&|[;|\n]|&(?!>)(?<![<>]&)")
# "> file", "2>> file", "&> file", ">&2" -> captures the target ("&2" = another stream, harmless)
_REDIRECT = re.compile(r"(?:\d|&)?>{1,2}\|?\s*(&\d+|&-|[^\s;|&<>]+)")


def classify_shell(command: str, safe_programs: set[str]) -> Decision:
    cmd = command.strip()
    if not cmd:
        return NEVER("empty command")
    for pattern, reason in _NEVER_SHELL:
        if re.search(pattern, cmd):
            return NEVER(reason)
    if "$(" in cmd or "`" in cmd or "<(" in cmd:
        return ASK("command substitution can't be checked in advance")
    decisions = []
    for seg in _SEGMENT_SPLIT.split(cmd):
        seg = seg.strip()
        if not seg:
            continue
        decisions.append(_classify_segment(seg, safe_programs))
    return _worst(*decisions) if decisions else NEVER("nothing to run")


def _classify_segment(seg: str, safe_programs: set[str]) -> Decision:
    try:
        tokens = shlex.split(seg)
    except ValueError:
        return ASK("unparseable command")
    while tokens and re.match(r"^\w+=", tokens[0]):  # FOO=bar cmd
        tokens = tokens[1:]
    if not tokens:
        return ASK("only variable assignments")
    prog = Path(tokens[0]).name
    for target in _REDIRECT.findall(seg):
        if not (target.startswith("&") or target == "/dev/null"):
            return ASK(f"output is written to file '{target}'")
    if prog == "find" and any(t in {"-delete", "-exec", "-execdir", "-ok"} for t in tokens):
        return ASK("find with -delete/-exec changes files")
    if prog in safe_programs:
        return SAFE(f"'{prog}' is read-only / opens an app")
    if prog in _ASK_PROGRAMS:
        return ASK(f"'{prog}' changes files, settings, software, or talks to the network")
    return ASK(f"'{prog}' is not on the known-safe list")


# ---------- GUI ----------
_RISKY_UI_WORDS = re.compile(
    r"\b(send|submit|delete|remove|erase|uninstall|install|buy|purchase|pay|checkout|order|post|publish|"
    r"share|confirm|agree|accept|transfer|format|reset|sign\s*out|log\s*out|empty\s+trash|apply|save)\b",
    re.IGNORECASE,
)


def classify_ui_target(description: str) -> Decision:
    if _RISKY_UI_WORDS.search(description or ""):
        return ASK(f"UI element looks consequential: '{description}'")
    return SAFE("ordinary UI interaction")


# ---------- the policy object ----------
_SENSITIVE = (".ssh", ".gnupg", ".password-store", ".mozilla/firefox", ".config/odeifried", ".local/state/odeifried")
_SYSTEM_DIRS = {Path(p) for p in ("/", "/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/opt", "/proc",
                                  "/root", "/sbin", "/srv", "/sys", "/usr", "/var", "/home")}


class Policy:
    def __init__(self, writable_roots: list[Path], safe_programs: set[str], home: Path | None = None):
        self.home = (home or Path.home()).resolve()
        self.roots = [Path(r).resolve() for r in writable_roots]
        self.safe_programs = set(safe_programs)

    @classmethod
    def from_config(cls, cfg) -> "Policy":
        return cls(cfg.writable_roots, set(cfg.get("safety", "safe_commands", [])))

    # path helpers
    def _resolve(self, p: str) -> Path:
        return Path(p).expanduser().resolve()

    def _sensitive(self, p: Path) -> bool:
        return any(p == self.home / s or (self.home / s) in p.parents for s in _SENSITIVE)

    def _writable(self, p: Path) -> bool:
        return any(p == r or r in p.parents for r in self.roots) and not self._sensitive(p)

    def _critical(self, p: Path) -> bool:
        return p in _SYSTEM_DIRS or p == self.home

    def check_read(self, path: str) -> Decision:
        p = self._resolve(path)
        if self._sensitive(p) or str(p) in {"/etc/shadow", "/etc/gshadow"}:
            return ASK(f"reading sensitive location {p}")
        return SAFE("reading")

    def check_write(self, path: str, overwrite: bool = False) -> Decision:
        p = self._resolve(path)
        if self._critical(p):
            return NEVER(f"refusing to modify {p}")
        if not self._writable(p):
            return ASK(f"{p} is outside your writable folders")
        if overwrite:
            return ASK(f"{p} already exists and would be overwritten")
        return SAFE("reversible change inside your folders")

    def check_delete(self, path: str) -> Decision:
        p = self._resolve(path)
        if self._critical(p) or p in self.roots:
            return NEVER(f"refusing to delete {p}")
        return ASK(f"delete {p} (moved to Trash, undoable)")

    def check(self, tool: str, args: dict) -> Decision:
        """Classify one tool call. Unknown tools are NEVER."""
        a = args or {}
        try:
            match tool:
                case "list_dir" | "read_file" | "file_info":
                    return self.check_read(a["path"])
                case "make_dir":
                    return self.check_write(a["path"])
                case "move":
                    return _worst(self.check_write(a["src"]),
                                  self.check_write(a["dst"], overwrite=Path(a["dst"]).expanduser().exists()))
                case "batch_move":
                    moves = a["moves"]
                    if not moves:
                        return SAFE("nothing to move")
                    return _worst(*(self.check("move", m) for m in moves))
                case "organize_by_type":
                    return self.check_write(a["path"])
                case "delete":
                    return self.check_delete(a["path"])
                case "run_shell":
                    return classify_shell(a["command"], self.safe_programs)
                case "open_app" | "open_url" | "web_search" | "screenshot" | "list_ui" | "wait" | "done":
                    return SAFE("viewing / opening")
                case "answer" | "undo_last" | "play_music" | "media_control" | "set_volume" | "set_theme":
                    return SAFE("harmless and instantly reversible")
                case "power":
                    action = str(a["action"]).lower()
                    if action in {"lock", "sleep"}:
                        return SAFE(f"{action} loses no work")
                    if action in {"shutdown", "restart", "logout"}:
                        return ASK(f"{action} closes your apps; unsaved work would be lost")
                    return NEVER(f"unknown power action '{action}'")
                case "install_app":
                    return ASK(f"install the app '{a['name']}'")
                case "empty_trash":
                    return ASK("permanently delete everything in the Trash (cannot be undone)")
                case "click_element" | "click_xy" | "describe_and_click":
                    return classify_ui_target(a.get("name") or a.get("description") or "")
                case "type_text":
                    return SAFE("typing text")
                case "press_key":
                    k = str(a.get("key", "")).lower()
                    if "ctrl+alt" in k or "sysrq" in k:
                        return NEVER(f"system key combo {k}")
                    return SAFE("key press")
                case _:
                    return NEVER(f"unknown tool '{tool}'")
        except (KeyError, TypeError) as e:
            return NEVER(f"malformed arguments for {tool}: {e}")
