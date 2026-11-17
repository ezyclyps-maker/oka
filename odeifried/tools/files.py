"""File-system tools. Every change is journaled so `odeifried undo` can reverse it."""
from __future__ import annotations

import shutil
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote

from . import Tool

CATEGORIES = {
    "Documents": {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".epub"},
    "Spreadsheets": {".xls", ".xlsx", ".ods", ".csv"},
    "Presentations": {".ppt", ".pptx", ".odp", ".key"},
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".heic", ".tiff", ".ico"},
    "Videos": {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"},
    "Audio": {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus"},
    "Archives": {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".zst"},
    "Installers": {".deb", ".rpm", ".appimage", ".flatpakref", ".snap", ".exe", ".msi", ".dmg", ".run"},
    "Code": {".py", ".js", ".ts", ".html", ".css", ".json", ".sh", ".c", ".cpp", ".h", ".java", ".go", ".rs", ".yaml", ".yml", ".toml"},
}
_IN_PROGRESS = {".part", ".crdownload", ".download", ".tmp"}


def category_of(p: Path) -> str:
    suffixes = "".join(p.suffixes[-2:]).lower()
    if suffixes in {".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst"}:
        return "Archives"
    ext = p.suffix.lower()
    return next((c for c, exts in CATEGORIES.items() if ext in exts), "Other")


def _free_name(dst: Path) -> Path:
    """'report.pdf' taken -> 'report (1).pdf'. Never overwrites."""
    if not dst.exists():
        return dst
    ext = "".join(dst.suffixes[-2:]) if category_of(dst) == "Archives" and len(dst.suffixes) > 1 else dst.suffix
    stem = dst.name[: len(dst.name) - len(ext)] if ext else dst.name
    i = 1
    while True:
        cand = dst.with_name(f"{stem} ({i}){ext}")
        if not cand.exists():
            return cand
        i += 1


def _mkdir(ctx, p: Path) -> None:
    missing = []
    q = p
    while not q.exists():
        missing.append(q)
        q = q.parent
    p.mkdir(parents=True, exist_ok=True)
    for m in reversed(missing):
        ctx.journal.record("mkdir", path=str(m))


def _move(ctx, src: Path, dst: Path) -> Path:
    if not src.exists():
        raise FileNotFoundError(src)
    if dst.is_dir() and not src.is_dir():
        dst = dst / src.name
    _mkdir(ctx, dst.parent)
    dst = _free_name(dst)
    shutil.move(str(src), str(dst))
    if not dst.exists() or src.exists():          # verify
        raise RuntimeError(f"move verification failed for {src}")
    ctx.journal.record("move", src=str(src), dst=str(dst))
    return dst


# ---- tool functions ----
def list_dir(ctx, path: str) -> str:
    p = Path(path).expanduser()
    entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    dirs = [e.name + "/" for e in entries if e.is_dir()]
    files = [e for e in entries if e.is_file()]
    kinds = Counter(category_of(f) for f in files)
    shown = [f.name for f in files[:60]]
    out = [f"{p}: {len(dirs)} folders, {len(files)} files",
           "folders: " + (", ".join(dirs[:40]) or "(none)"),
           "file types: " + (", ".join(f"{k}={v}" for k, v in kinds.most_common()) or "(none)"),
           "files: " + (", ".join(shown) or "(none)") + (" ..." if len(files) > 60 else "")]
    return "\n".join(out)


def read_file(ctx, path: str) -> str:
    data = Path(path).expanduser().read_text(errors="replace")
    return data[:4000] + ("\n...[truncated]" if len(data) > 4000 else "")


def file_info(ctx, path: str) -> str:
    p = Path(path).expanduser()
    st = p.stat()
    return f"{p}: {'folder' if p.is_dir() else category_of(p)}, {st.st_size} bytes, modified {time.ctime(st.st_mtime)}"


def make_dir(ctx, path: str) -> str:
    p = Path(path).expanduser()
    _mkdir(ctx, p)
    return f"VERIFIED folder exists: {p}"


def move(ctx, src: str, dst: str) -> str:
    final = _move(ctx, Path(src).expanduser(), Path(dst).expanduser())
    return f"VERIFIED moved {src} -> {final}"


def batch_move(ctx, moves: list) -> str:
    done = [str(_move(ctx, Path(m["src"]).expanduser(), Path(m["dst"]).expanduser())) for m in moves]
    return f"VERIFIED moved {len(done)} items"


def organize_by_type(ctx, path: str) -> str:
    root = Path(path).expanduser()
    moved = Counter()
    for f in sorted(root.iterdir()):
        if not f.is_file() or f.name.startswith(".") or f.suffix.lower() in _IN_PROGRESS:
            continue
        _move(ctx, f, root / category_of(f) / f.name)
        moved[category_of(f)] += 1
    left = [f.name for f in root.iterdir() if f.is_file() and not f.name.startswith(".")]
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(moved.items())) or "nothing to move"
    status = "VERIFIED" if all(Path(n).suffix.lower() in _IN_PROGRESS for n in left) else "PARTIAL"
    return f"{status} organized {sum(moved.values())} files in {root} ({summary}). Loose files left: {left or 'none'}"


def delete(ctx, path: str) -> str:
    """Freedesktop Trash, so it shows up in the Files app's Trash and is undoable."""
    src = Path(path).expanduser().resolve()
    trash = Path.home() / ".local/share/Trash"
    (trash / "files").mkdir(parents=True, exist_ok=True)
    (trash / "info").mkdir(parents=True, exist_ok=True)
    dst = _free_name(trash / "files" / src.name)
    info = trash / "info" / (dst.name + ".trashinfo")
    info.write_text(f"[Trash Info]\nPath={quote(str(src))}\nDeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    shutil.move(str(src), str(dst))
    ctx.journal.record("trash", src=str(src), dst=str(dst), info=str(info))
    return f"VERIFIED moved to Trash: {src}"


_P = {"type": "string", "description": "absolute path or ~/..."}
TOOLS = [
    Tool("list_dir", "List a folder: subfolders, counts of file types, file names.", {"path": _P}, ["path"], list_dir),
    Tool("read_file", "Read the start of a text file.", {"path": _P}, ["path"], read_file),
    Tool("file_info", "Size, type and date of a file.", {"path": _P}, ["path"], file_info),
    Tool("make_dir", "Create a folder.", {"path": _P}, ["path"], make_dir),
    Tool("move", "Move or rename one file/folder. If dst is a folder, the file goes inside it.",
         {"src": _P, "dst": _P}, ["src", "dst"], move),
    Tool("batch_move", "Move many files in one call (faster than many move calls).",
         {"moves": {"type": "array", "items": {"type": "object", "properties": {"src": _P, "dst": _P}}}},
         ["moves"], batch_move),
    Tool("organize_by_type", "Built-in skill: sort the loose files of a folder into subfolders by type "
         "(Documents, Images, Videos, Audio, Archives, Installers, Code, Spreadsheets, Presentations, Other).",
         {"path": _P}, ["path"], organize_by_type),
    Tool("delete", "Move a file or folder to the Trash.", {"path": _P}, ["path"], delete),
]
