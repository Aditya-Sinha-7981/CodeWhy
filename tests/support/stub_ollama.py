"""A tiny stand-in for the Ollama HTTP API, for CI and wheel smoke tests (stdlib only).

    python tests/support/stub_ollama.py --port 0 --model qwen2.5-coder:14b

Prints `listening on <port>` once ready. Implements what codewhy calls:

- `GET /api/version` -> {"version": "0.34.4"}
- `GET /api/tags`    -> the configured model
- `POST /api/chat`   -> a schema-valid JSON object (as a string in message.content)
  built from the request's `format` schema, with text derived from a hash of
  the prompt, so the same prompt always gets the same answer.

Plus `GET /stub/stats` -> {"chat_calls": N} so a test can assert that a re-run
was served from codewhy's cache. Nothing is logged: no prompts, no answers.
No production code imports this file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

STUB_VERSION = "0.34.4"


def value_for(schema: dict[str, Any], digest: str, name: str = "value") -> Any:
    """A deterministic value satisfying `schema`'s required fields."""
    kind = schema.get("type")
    if kind == "object":
        properties = schema.get("properties", {})
        return {key: value_for(properties.get(key, {}), digest, key) for key in schema.get("required", [])}
    if kind == "array":
        return []
    if kind == "integer":
        return 0
    if kind == "number":
        return 0.0
    if kind == "boolean":
        return False
    if "enum" in schema:
        return schema["enum"][0]
    return f"Stub {name.replace('_', ' ')} {digest}."


def chat_reply(body: dict[str, Any]) -> dict[str, Any]:
    messages = body.get("messages") or [{}]
    prompt = str(messages[-1].get("content", ""))
    schema = body.get("format")
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    data = value_for(schema, digest) if isinstance(schema, dict) else {"answer": f"Stub answer {digest}."}
    return {
        "model": body.get("model", ""),
        "message": {"role": "assistant", "content": json.dumps(data)},
        "done": True,
        "done_reason": "stop",
    }


class StubState:
    def __init__(self, model: str) -> None:
        self.model = model
        self.chat_calls = 0
        self.lock = threading.Lock()


def make_handler(state: StubState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - silence access logs
            return

        def _send(self, status: int, payload: object) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if self.path == "/api/version":
                self._send(200, {"version": STUB_VERSION})
            elif self.path == "/api/tags":
                self._send(200, {"models": [{"name": state.model, "model": state.model}]})
            elif self.path == "/stub/stats":
                with state.lock:
                    self._send(200, {"chat_calls": state.chat_calls})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            if self.path != "/api/chat":
                self._send(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send(400, {"error": "invalid JSON body"})
                return
            if body.get("model") != state.model:
                self._send(404, {"error": f"model '{body.get('model')}' not found"})
                return
            with state.lock:
                state.chat_calls += 1
            self._send(200, chat_reply(body))

    return Handler


def serve(port: int, model: str) -> tuple[ThreadingHTTPServer, StubState]:
    """Start the stub on 127.0.0.1 in a background thread; returns (server, state)."""
    state = StubState(model)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--port", type=int, default=0, help="0 picks a free port")
    parser.add_argument("--model", default="qwen2.5-coder:14b")
    args = parser.parse_args(argv)
    server, _ = serve(args.port, args.model)
    print(f"listening on {server.server_address[1]}", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
