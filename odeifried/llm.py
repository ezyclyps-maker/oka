"""Minimal Ollama client (standard library only): chat with tool calling, images and streaming."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Iterator


class LLMError(RuntimeError):
    pass


class Ollama:
    def __init__(self, url: str, temperature: float = 0.1, timeout: int = 300, think: bool = False,
                 keep_alive: str = "2h"):
        self.url = url.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout
        self.think = think
        self.keep_alive = keep_alive

    # ---- low level
    def _open(self, path: str, body: dict | None = None, timeout: float | None = None):
        req = urllib.request.Request(
            self.url + path, data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"})
        try:
            return urllib.request.urlopen(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as e:
            raise LLMError(f"AI engine error {e.code}: {e.read().decode(errors='replace')[:300]}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise LLMError(f"cannot reach the AI engine at {self.url}: {e}") from e

    def _body(self, model: str, messages: list[dict], tools, stream: bool, max_tokens: int | None) -> dict:
        options = {"temperature": self.temperature, "num_ctx": 8192}
        if max_tokens:
            options["num_predict"] = max_tokens
        body = {"model": model, "messages": messages, "stream": stream,
                "keep_alive": self.keep_alive, "options": options}
        if tools:
            body["tools"] = tools
        if not self.think:
            body["think"] = False
        return body

    def _retry_without_think(self, body: dict, err: LLMError) -> bool:
        if "think" in body and "think" in str(err).lower():
            body.pop("think")
            return True
        return False

    # ---- API
    def chat(self, model: str, messages: list[dict], tools: list[dict] | None = None,
             max_tokens: int | None = None) -> dict:
        body = self._body(model, messages, tools, False, max_tokens)
        try:
            with self._open("/api/chat", body) as r:
                return json.loads(r.read())["message"]
        except LLMError as e:
            if not self._retry_without_think(body, e):
                raise
            with self._open("/api/chat", body) as r:
                return json.loads(r.read())["message"]

    def chat_stream(self, model: str, messages: list[dict]) -> Iterator[str]:
        """Yield the reply piece by piece (for the Chat tab)."""
        body = self._body(model, messages, None, True, None)
        try:
            resp = self._open("/api/chat", body)
        except LLMError as e:
            if not self._retry_without_think(body, e):
                raise
            resp = self._open("/api/chat", body)
        with resp:
            for line in resp:
                if not line.strip():
                    continue
                piece = json.loads(line)
                if piece.get("error"):
                    raise LLMError(piece["error"])
                text = piece.get("message", {}).get("content", "")
                if text:
                    yield text
                if piece.get("done"):
                    return

    def warm(self, model: str, messages: list[dict], tools: list[dict] | None = None) -> None:
        """Load the model and pre-read the fixed part of the prompt, so the first real request is fast."""
        self.chat(model, messages, tools, max_tokens=1)

    def models(self) -> list[str]:
        with self._open("/api/tags", timeout=10) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]

    def pull(self, model: str) -> Iterator[dict]:
        """Download a model; yields progress dicts {status, total, completed}."""
        with self._open("/api/pull", {"model": model, "stream": True}, timeout=3600) as resp:
            for line in resp:
                if line.strip():
                    piece = json.loads(line)
                    if piece.get("error"):
                        raise LLMError(piece["error"])
                    yield piece


def has_model(installed: list[str], wanted: str) -> bool:
    return any(m == wanted or m == wanted + ":latest" or m.split(":")[0] == wanted for m in installed)


_JSON_CALL = re.compile(r"\{.*\}", re.S)


def tool_calls(message: dict, known: set[str] | None = None) -> list[tuple[str, dict]]:
    """Normalize tool calls to [(name, args)]. Also accepts a call written as JSON text, which
    small models sometimes do instead of using the proper tool-call channel."""
    out = []
    for call in message.get("tool_calls") or []:
        fn = call.get("function", {})
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"_raw": args}
        out.append((fn.get("name", ""), args))
    if out or not known:
        return out
    m = _JSON_CALL.search(message.get("content") or "")
    if m:
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            return out
        name = obj.get("name") or obj.get("tool")
        args = obj.get("arguments") or obj.get("parameters") or obj.get("args") or {}
        if name in known and isinstance(args, dict):
            out.append((name, args))
    return out
