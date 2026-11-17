"""Checks the request we send Ollama and how replies are parsed, using a fake local server."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from odeifried.llm import LLMError, Ollama, tool_calls


@pytest.fixture
def server():
    received = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(body)
            reply = {"message": {"role": "assistant", "content": "",
                                 "tool_calls": [{"function": {"name": "list_dir", "arguments": {"path": "~"}}}]}}
            data = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            data = json.dumps({"models": [{"name": "qwen3:4b"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", received
    srv.shutdown()


def test_chat_with_tools(server):
    url, received = server
    msg = Ollama(url).chat("qwen3:4b", [{"role": "user", "content": "hi"}], [{"type": "function"}])
    assert tool_calls(msg) == [("list_dir", {"path": "~"})]
    assert received[0]["think"] is False and received[0]["stream"] is False and received[0]["tools"]


def test_models(server):
    assert Ollama(server[0]).models() == ["qwen3:4b"]


def test_unreachable():
    with pytest.raises(LLMError):
        Ollama("http://127.0.0.1:9", timeout=2).chat("m", [])


def test_string_arguments_are_parsed():
    msg = {"tool_calls": [{"function": {"name": "move", "arguments": '{"src": "a", "dst": "b"}'}}]}
    assert tool_calls(msg) == [("move", {"src": "a", "dst": "b"})]
