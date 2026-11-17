"""Tool registry. A tool = a Python function + a JSON schema the model sees.
Tools never decide permissions themselves: the agent asks safety.Policy first."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..config import Config
from ..journal import Journal


@dataclass
class ToolContext:
    cfg: Config
    journal: Journal
    llm: object | None = None          # used by the vision fallback
    ui_cache: dict = field(default_factory=dict)  # element id -> AT-SPI accessible


@dataclass
class Tool:
    name: str
    description: str
    params: dict                      # JSON-schema "properties"
    required: list[str]
    fn: Callable[..., str]

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.params, "required": self.required}}}


def all_tools(gui: bool = True) -> dict[str, Tool]:
    from . import files, shell
    tools = files.TOOLS + shell.TOOLS
    if gui:
        from ..control import desktop
        tools += desktop.TOOLS
    tools.append(Tool("done", "Call when the task is finished (or impossible). Summarize what you did.",
                      {"summary": {"type": "string"}}, ["summary"], lambda ctx, summary: summary))
    return {t.name: t for t in tools}
