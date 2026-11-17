from pathlib import Path

from odeifried.journal import Journal, undo_last
from odeifried.tools import ToolContext
from odeifried.tools import files


def _ctx(cfg):
    return ToolContext(cfg, Journal(cfg.state_dir, "t1"))


def _make(dl: Path, names):
    for n in names:
        (dl / n).write_text("x")


NAMES = ["report.pdf", "photo.JPG", "song.mp3", "backup.tar.gz", "app.deb", "data.csv", "notes",
         "half.crdownload", ".hidden"]


def test_category_of():
    assert files.category_of(Path("a.tar.gz")) == "Archives"
    assert files.category_of(Path("a.JPG")) == "Images"
    assert files.category_of(Path("noext")) == "Other"


def test_organize_then_undo_restores_everything(cfg):
    dl = Path.home() / "Downloads"
    _make(dl, NAMES)
    before = sorted(p.name for p in dl.iterdir())
    out = files.organize_by_type(_ctx(cfg), str(dl))
    assert out.startswith("VERIFIED"), out
    assert (dl / "Documents/report.pdf").exists()
    assert (dl / "Images/photo.JPG").exists()
    assert (dl / "Archives/backup.tar.gz").exists()
    assert (dl / "Installers/app.deb").exists()
    assert (dl / "Other/notes").exists()
    assert (dl / "half.crdownload").exists()      # in-progress downloads are left alone
    assert (dl / ".hidden").exists()
    report = undo_last(cfg.state_dir)
    assert sorted(p.name for p in dl.iterdir()) == before, report
    assert undo_last(cfg.state_dir) == ["Nothing to undo."]


def test_name_collision_never_overwrites(cfg):
    dl = Path.home() / "Downloads"
    (dl / "Documents").mkdir()
    (dl / "Documents/report.pdf").write_text("old")
    (dl / "report.pdf").write_text("new")
    files.organize_by_type(_ctx(cfg), str(dl))
    assert (dl / "Documents/report.pdf").read_text() == "old"
    assert (dl / "Documents/report (1).pdf").read_text() == "new"


def test_delete_goes_to_trash_and_undo_restores(cfg):
    f = Path.home() / "Downloads/old.txt"
    f.write_text("keep me")
    out = files.delete(_ctx(cfg), str(f))
    assert "Trash" in out and not f.exists()
    assert list((Path.home() / ".local/share/Trash/files").iterdir())
    undo_last(cfg.state_dir)
    assert f.read_text() == "keep me"


def test_undo_skips_files_changed_later(cfg):
    dl = Path.home() / "Downloads"
    _make(dl, ["a.pdf"])
    files.organize_by_type(_ctx(cfg), str(dl))
    (dl / "Documents/a.pdf").unlink()             # user deleted it afterwards
    report = undo_last(cfg.state_dir)
    assert any("SKIPPED" in r for r in report)


def test_list_dir_summary(cfg):
    dl = Path.home() / "Downloads"
    _make(dl, ["a.pdf", "b.png"])
    out = files.list_dir(_ctx(cfg), str(dl))
    assert "2 files" in out and "Documents=1" in out and "Images=1" in out
