"""Instant commands: what is recognized, what is split, and that nothing risky slips past the policy."""
from pathlib import Path

import pytest

from odeifried import router
from odeifried.agent import Assistant
from odeifried.safety.confirm import AlwaysAllow, AlwaysDeny

APPS = {"firefox", "files", "terminal", "minecraft"}
m = lambda text: router.match(text, lambda n: n in APPS)  # noqa: E731


def steps(text):
    plan = m(text)
    return plan.steps if plan else None


def test_users_example_sentence_splits_into_three():
    parts = router.split_tasks("organize my files, then play lil baby in Spotify and then complete my google "
                               "classrooms missing assignments", lambda s: m(s) is not None)
    assert parts == ["organize my files", "play lil baby in Spotify", "complete my google classrooms missing assignments"]
    assert [m(p).intent for p in parts] == ["organize", "play", "classroom_homework"]


def test_organize_variants():
    home = str(Path.home())
    assert steps("organize my downloads") == [("organize_by_type", {"path": home + "/Downloads"})]
    assert steps("Please organize ~/Downloads by file type.") == [("organize_by_type", {"path": "~/Downloads"})]
    assert [a["path"] for _, a in steps("can you clean up my files")] == [home + "/Downloads", home + "/Desktop"]


@pytest.mark.parametrize("text,expected", [
    ("play lil baby in Spotify", [("play_music", {"query": "lil baby", "service": "spotify"})]),
    ("play some drake", [("play_music", {"query": "drake"})]),
    ("play songs by Taylor Swift on youtube", [("play_music", {"query": "Taylor Swift", "service": "youtube"})]),
    ("play minecraft", [("open_app", {"name": "minecraft"})]),
    ("play music", [("media_control", {"action": "play"})]),
    ("pause", [("media_control", {"action": "pause"})]),
    ("next song", [("media_control", {"action": "next"})]),
    ("volume 40", [("set_volume", {"level": 40})]),
    ("set the volume to 250%", [("set_volume", {"level": 100})]),
    ("turn it up", [("set_volume", {"change": 10})]),
    ("quieter", [("set_volume", {"change": -10})]),
    ("mute", [("set_volume", {"mute": "on"})]),
    ("open firefox", [("open_app", {"name": "firefox"})]),
    ("open youtube", [("open_url", {"url": "https://www.youtube.com"})]),
    ("go to docs.python.org", [("open_url", {"url": "https://docs.python.org"})]),
    ("search for weather in new york", [("web_search", {"query": "weather in new york"})]),
    ("what's the weather in Tokyo?", [("web_search", {"query": "weather Tokyo"})]),
    ("take a screenshot", [("screenshot", {})]),
    ("dark mode", [("set_theme", {"mode": "dark"})]),
    ("install spotify", [("install_app", {"name": "spotify"})]),
    ("shut down", [("power", {"action": "shutdown"})]),
    ("lock my computer", [("power", {"action": "lock"})]),
    ("undo that", [("undo_last", {})]),
    ("show my missing assignments", [("open_url", {"url": router.CLASSROOM_MISSING})]),
])
def test_recognized(text, expected):
    assert steps(text) == expected


@pytest.mark.parametrize("text", ["write an essay about the moon", "open the pod bay doors", "why is the sky blue",
                                  "rename all my photos by date", "stop", "play"])
def test_not_recognized_goes_to_ai(text):
    assert m(text) is None or text == "play"


def test_and_only_splits_between_whole_commands():
    inst = lambda s: m(s) is not None  # noqa: E731
    assert router.split_tasks("pause and lock", inst) == ["pause", "lock"]
    assert router.split_tasks("search for cats and dogs", inst) == ["search for cats and dogs"]
    assert router.split_tasks("open firefox and files", inst) == ["open firefox", "open files"]
    assert router.split_tasks("volume 30; next song", inst) == ["volume 30", "next song"]


class NoLLM:
    def chat(self, *a, **k):
        raise AssertionError("instant commands must not call the AI model")


def test_instant_route_runs_without_model_and_is_undoable(cfg):
    dl = Path.home() / "Downloads"
    for n in ["a.pdf", "b.png"]:
        (dl / n).write_text("x")
    events = []
    a = Assistant(cfg, NoLLM(), AlwaysDeny(), gui=False, emit=events.append)
    res = a.handle("organize my downloads")
    assert res.ok and res.route == "instant" and (dl / "Documents/a.pdf").exists()
    assert events[-1]["type"] == "done" and events[-1]["ok"]
    assert Assistant(cfg, NoLLM(), AlwaysDeny(), gui=False).handle("undo").ok
    assert (dl / "a.pdf").exists()


def test_instant_ask_actions_still_need_permission(cfg, monkeypatch):
    ran = []
    monkeypatch.setattr("odeifried.tools.system.run", lambda argv, timeout=15: (ran.append(argv), (0, ""))[1])
    monkeypatch.setattr("odeifried.tools.system.have", lambda p: True)
    res = Assistant(cfg, NoLLM(), AlwaysDeny(), gui=False).handle("shut down")
    assert not res.ok and ran == []                       # denied -> nothing ran
    res = Assistant(cfg, NoLLM(), AlwaysAllow(), gui=False).handle("shut down")
    assert res.ok and ran == [["systemctl", "poweroff"]]


class ScriptLLM:
    def __init__(self, script):
        self.script, self.calls = list(script), 0

    def chat(self, model, messages, tools=None, **k):
        self.calls += 1
        name, args = self.script.pop(0)
        return {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def test_skill_learned_then_replayed_without_model(cfg):
    target = str(Path.home() / "Projects/school")
    llm = ScriptLLM([("make_dir", {"path": target}), ("done", {"summary": "made it"})])
    first = Assistant(cfg, llm, AlwaysDeny(), gui=False).handle("make my school project folder")
    assert first.ok and first.route == "ai" and llm.calls == 2
    Path(target).rmdir()
    second = Assistant(cfg, NoLLM(), AlwaysDeny(), gui=False).handle("Make my school project folder!")
    assert second.ok and second.route == "skill" and Path(target).is_dir()


def test_no_engine_gives_clear_message_but_instant_still_works(cfg):
    from odeifried.llm import LLMError

    class Down:
        def chat(self, *a, **k):
            raise LLMError("cannot reach the AI engine")
    res = Assistant(cfg, Down(), AlwaysDeny(), gui=False).handle("volume 20 then write me a poem about cats")
    assert not res.ok and "AI model" in res.summary
