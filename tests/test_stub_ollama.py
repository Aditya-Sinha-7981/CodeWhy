"""The stub Ollama server used by CI's wheel job (task 09 Requirement 2). Loopback only; no real model."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from codewhy.summarize.schemas import FilePass, ProjectPass, provider_schema
from tests.helpers import make_project, run_cli
from tests.support.stub_ollama import STUB_VERSION, serve

STUB = Path(__file__).parent / "support" / "stub_ollama.py"
MODEL = "qwen2.5-coder:14b"


@pytest.fixture
def stub() -> Iterator[tuple[str, object]]:
    server, state = serve(0, MODEL)
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", state
    finally:
        server.shutdown()
        server.server_close()


def chat(base: str, schema: dict, prompt: str = "hello", model: str = MODEL) -> httpx.Response:
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False, "format": schema}
    return httpx.post(f"{base}/api/chat", json=body, timeout=5)


def test_version_and_tags(stub) -> None:
    base, _ = stub
    assert httpx.get(f"{base}/api/version", timeout=5).json() == {"version": STUB_VERSION}
    assert httpx.get(f"{base}/api/tags", timeout=5).json() == {"models": [{"name": MODEL, "model": MODEL}]}
    assert httpx.get(f"{base}/nope", timeout=5).status_code == 404


@pytest.mark.parametrize("model", [FilePass, ProjectPass])
def test_chat_honors_the_requested_format_schema(stub, model) -> None:
    base, state = stub
    response = chat(base, provider_schema(model))
    assert response.status_code == 200
    payload = response.json()
    assert payload["done"] is True and payload["done_reason"] == "stop"
    model.model_validate(json.loads(payload["message"]["content"]))
    assert state.chat_calls == 1


def test_chat_is_deterministic_and_counted(stub) -> None:
    base, _ = stub
    schema = provider_schema(FilePass)
    first, again, other = chat(base, schema, "a"), chat(base, schema, "a"), chat(base, schema, "b")
    assert first.json() == again.json() != other.json()
    assert httpx.get(f"{base}/stub/stats", timeout=5).json() == {"chat_calls": 3}


def test_unknown_model_and_bad_body(stub) -> None:
    base, state = stub
    assert chat(base, provider_schema(FilePass), model="other:1b").status_code == 404
    assert httpx.post(f"{base}/api/chat", content=b"{not json", timeout=5).status_code == 400
    assert state.chat_calls == 0


def test_codewhy_scans_against_the_stub_and_reruns_from_cache(stub, tmp_path: Path) -> None:
    base, state = stub
    root = make_project(tmp_path, {"main.py": "from util import f\n\nf()\n", "util.py": "def f():\n    return 1\n"})
    (root / "codewhy.toml").write_text(f'[llm]\nhost = "{base}"\n', encoding="utf-8")
    first = run_cli(["scan", str(root), "--quiet"])
    assert first.exit_code == 0, first.stderr
    assert (root / "PROJECT_DOCS.md").read_text(encoding="utf-8").startswith("---\ngenerated_by: codewhy v")
    calls = state.chat_calls
    assert calls == 4
    second = run_cli(["scan", str(root), "--quiet"])
    assert second.exit_code == 0 and state.chat_calls == calls


def test_script_starts_reports_its_port_and_stops_cleanly() -> None:
    process = subprocess.Popen([sys.executable, str(STUB), "--port", "0"], stdout=subprocess.PIPE, text=True)
    try:
        line = process.stdout.readline().strip()
        assert line.startswith("listening on ")
        port = int(line.rsplit(" ", 1)[1])
        assert httpx.get(f"http://127.0.0.1:{port}/api/version", timeout=5).status_code == 200
    finally:
        process.terminate()
        process.wait(timeout=10)
    with pytest.raises(httpx.ConnectError):
        httpx.get(f"http://127.0.0.1:{port}/api/version", timeout=2)
