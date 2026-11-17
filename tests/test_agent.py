"""Agent loop tests with a scripted fake model (no Ollama needed)."""
import json
from pathlib import Path

from odeifried.agent import Agent
from odeifried.safety.confirm import AlwaysAllow, AlwaysDeny


class FakeLLM:
    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def chat(self, model, messages, tools=None):
        self.seen.append(messages[-1])
        if not self.script:
            return {"content": "I'm finished."}
        name, args = self.script.pop(0)
        return {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def _logs(cfg):
    lines = []
    for f in cfg.log_dir.glob("*.jsonl"):
        lines += [json.loads(l) for l in f.read_text().splitlines()]
    return lines


def test_organize_downloads_end_to_end(cfg):
    dl = Path.home() / "Downloads"
    for n in ["a.pdf", "b.png", "c.zip"]:
        (dl / n).write_text("x")
    llm = FakeLLM([("list_dir", {"path": str(dl)}), ("organize_by_type", {"path": str(dl)}),
                   ("done", {"summary": "sorted 3 files"})])
    res = Agent(cfg, llm, AlwaysDeny(), gui=False, echo=lambda *_: None).run("organize ~/Downloads by file type")
    assert res.ok and res.steps == 3
    assert (dl / "Documents/a.pdf").exists() and (dl / "Images/b.png").exists() and (dl / "Archives/c.zip").exists()
    events = [e["event"] for e in _logs(cfg)]
    assert events[0] == "task_start" and events[-1] == "task_end"


def test_never_action_is_refused_and_not_run(cfg):
    llm = FakeLLM([("run_shell", {"command": "sudo rm -rf /"}), ("done", {"summary": "x"})])
    Agent(cfg, llm, AlwaysAllow(), gui=False, echo=lambda *_: None).run("x")
    assert "REFUSED" in llm.seen[1]["content"]
    assert not any(e["event"] == "confirmation" for e in _logs(cfg))   # never even asked


def test_ask_action_denied_is_not_run(cfg):
    f = Path.home() / "Downloads/keep.txt"
    f.write_text("x")
    llm = FakeLLM([("delete", {"path": str(f)}), ("done", {"summary": "x"})])
    Agent(cfg, llm, AlwaysDeny(), gui=False, echo=lambda *_: None).run("delete keep.txt")
    assert f.exists()
    assert "DENIED" in llm.seen[1]["content"]


def test_ask_action_allowed_runs(cfg):
    f = Path.home() / "Downloads/old.txt"
    f.write_text("x")
    llm = FakeLLM([("delete", {"path": str(f)}), ("done", {"summary": "x"})])
    Agent(cfg, llm, AlwaysAllow(), gui=False, echo=lambda *_: None).run("delete old.txt")
    assert not f.exists()


def test_unknown_tool_refused(cfg):
    llm = FakeLLM([("format_disk", {"dev": "/dev/sda"}), ("done", {"summary": "x"})])
    Agent(cfg, llm, AlwaysAllow(), gui=False, echo=lambda *_: None).run("x")
    assert "REFUSED" in llm.seen[1]["content"]


def test_kill_switch_stops_before_next_step(cfg):
    agent_holder = {}

    class StopAfterFirst(FakeLLM):
        def chat(self, model, messages, tools=None):
            if len(self.seen) == 1:
                agent_holder["a"].killswitch.flag.touch()   # user pressed the kill switch
            return super().chat(model, messages, tools)

    dl = Path.home() / "Downloads"
    llm = StopAfterFirst([("list_dir", {"path": str(dl)}), ("organize_by_type", {"path": str(dl)}),
                          ("done", {"summary": "x"})])
    (dl / "a.pdf").write_text("x")
    a = Agent(cfg, llm, AlwaysAllow(), gui=False, echo=lambda *_: None)
    agent_holder["a"] = a
    res = a.run("organize")
    assert not res.ok and "kill switch" in res.summary
    assert (dl / "a.pdf").exists()                 # organize never ran


def test_tool_errors_are_reported_not_crashing(cfg):
    llm = FakeLLM([("read_file", {"path": str(Path.home() / "nope.txt")}), ("done", {"summary": "x"})])
    res = Agent(cfg, llm, AlwaysAllow(), gui=False, echo=lambda *_: None).run("x")
    assert res.ok and llm.seen[1]["content"].startswith("ERROR")


def test_step_limit(cfg):
    cfg.raw["agent"]["max_steps"] = 3
    llm = FakeLLM([("list_dir", {"path": str(Path.home())})] * 10)
    res = Agent(cfg, llm, AlwaysAllow(), gui=False, echo=lambda *_: None).run("loop forever")
    assert not res.ok and res.steps == 3
