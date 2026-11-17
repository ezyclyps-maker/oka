"""Minimal Ollama client (standard library only). Talks to /api/chat with tool calling and images."""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class LLMError(RuntimeError):
    pass


class Ollama:
    def __init__(self, url: str, temperature: float = 0.1, timeout: int = 300, think: bool = False):
        self.url = url.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout
        self.think = think

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise LLMError(f"Ollama HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise LLMError(f"cannot reach Ollama at {self.url}: {e}") from e

    def chat(self, model: str, messages: list[dict], tools: list[dict] | None = None) -> dict:
        body = {"model": model, "messages": messages, "stream": False, "keep_alive": "30m",
                "options": {"temperature": self.temperature, "num_ctx": 8192}}
        if tools:
            body["tools"] = tools
        if not self.think:
            body["think"] = False
        try:
            return self._post("/api/chat", body)["message"]
        except LLMError as e:
            if "think" in body and "does not support thinking" in str(e):
                body.pop("think")
                return self._post("/api/chat", body)["message"]
            raise

    def models(self) -> list[str]:
        req = urllib.request.Request(self.url + "/api/tags")
        with urllib.request.urlopen(req, timeout=10) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]


def tool_calls(message: dict) -> list[tuple[str, dict]]:
    """Normalize Ollama tool calls to [(name, args_dict)]."""
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
    return out
