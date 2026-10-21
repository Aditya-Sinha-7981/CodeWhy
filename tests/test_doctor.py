from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

import codewhy.llm.provider as provider_module
from tests.helpers import make_project, run_cli, snapshot_tree

Handler = Callable[[httpx.Request], httpx.Response]
KEY = "sk-doctor-SECRET-1a2b3c4d"


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[Handler], list[httpx.Request]]]:
    """Route every provider the CLI builds through a MockTransport; returns the request log."""

    def install(handler: Handler) -> list[httpx.Request]:
        seen: list[httpx.Request] = []

        def logged(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        real = provider_module.build_provider

        def fake_build(config, **_ignored):
            return real(config, sleep=lambda s: None, client=httpx.Client(transport=httpx.MockTransport(logged)))

        monkeypatch.setattr("codewhy.cli.build_provider", fake_build)
        return seen

    yield install


def ollama_healthy(models: tuple[str, ...] = ("qwen2.5-coder:14b",)) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.34.4"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in models]})
        raise AssertionError(f"doctor must not call {request.url.path}")

    return handler


def test_doctor_reports_a_healthy_local_setup(tmp_path: Path, transport) -> None:
    seen = transport(ollama_healthy())
    make_project(tmp_path, {"main.py": "x = 1\n"})
    result = run_cli(["doctor", str(tmp_path)])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.splitlines() == [
        "provider: ollama",
        "model: qwen2.5-coder:14b",
        "endpoint: http://localhost:11434 (local)",
        "context_tokens: 16384",
        "max_output_tokens: 1024",
        "prompt budget: ~13824 tokens (estimated)",
        "ok: preflight passed",
    ]
    assert [r.method for r in seen] == ["GET", "GET"]


def test_doctor_reports_a_healthy_gemini_setup(tmp_path: Path, transport, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    seen = transport(lambda r: httpx.Response(200, json={"data": [{"id": "models/gem-x"}]}))
    result = run_cli(["doctor", str(tmp_path), "--provider", "gemini", "--model", "gem-x"])
    assert result.exit_code == 0, result.stderr
    assert "endpoint: generativelanguage.googleapis.com (remote)" in result.stdout
    assert "prompt budget: ~22118 tokens (estimated)" in result.stdout
    assert [str(r.url) for r in seen] == ["https://generativelanguage.googleapis.com/v1beta/openai/models"]
    assert KEY not in result.stdout + result.stderr


def test_doctor_ollama_down_says_ollama_serve(tmp_path: Path, transport) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    transport(down)
    result = run_cli(["doctor", str(tmp_path)])
    assert result.exit_code == 1
    assert result.stderr == (
        "error: Cannot reach Ollama at http://localhost:11434. Start it with `ollama serve` (or install from ollama.com).\n"
    )


def test_doctor_model_not_pulled_says_ollama_pull(tmp_path: Path, transport) -> None:
    transport(ollama_healthy(models=("llama3:latest",)))
    result = run_cli(["doctor", str(tmp_path), "--model", "qwen2.5-coder:7b"])
    assert result.exit_code == 1
    assert result.stderr == "error: Model 'qwen2.5-coder:7b' is not available locally. Run: ollama pull qwen2.5-coder:7b\n"


def test_doctor_bad_gemini_key(tmp_path: Path, transport, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    transport(lambda r: httpx.Response(400, json={"error": {"code": 400, "message": "Please pass a valid API key"}}))
    result = run_cli(["doctor", str(tmp_path), "--provider", "gemini", "--model", "gem-x"])
    assert result.exit_code == 1
    assert "rejected the key in GEMINI_API_KEY" in result.stderr
    assert KEY not in result.stdout + result.stderr


def test_doctor_missing_key_names_the_variable(tmp_path: Path, transport, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    seen = transport(lambda r: httpx.Response(200))
    result = run_cli(["doctor", str(tmp_path), "--provider", "gemini", "--model", "gem-x"])
    assert result.exit_code == 1
    assert "The environment variable GEMINI_API_KEY is not set" in result.stderr
    assert seen == []


def test_doctor_missing_model_for_gemini(tmp_path: Path, transport) -> None:
    seen = transport(lambda r: httpx.Response(200))
    result = run_cli(["doctor", str(tmp_path), "--provider", "gemini"])
    assert result.exit_code == 1
    assert "llm.model is required for provider 'gemini'" in result.stderr
    assert seen == []


@pytest.mark.parametrize("command", [["doctor"], ["scan"], ["config", "show"]])
def test_unknown_provider_exits_1_listing_valid_values(tmp_path: Path, command: list[str]) -> None:
    result = run_cli([*command, str(tmp_path), "--provider", "openai"])
    assert result.exit_code == 1
    assert "'ollama', 'openai-compat' or 'gemini'" in result.stderr


def test_doctor_writes_nothing_and_leaks_no_key(tmp_path: Path, transport, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    transport(lambda r: httpx.Response(200, json={"data": []}))
    make_project(tmp_path, {"main.py": "x = 1\n", "codewhy.toml": '[llm]\nprovider = "gemini"\nmodel = "gem-x"\n'})
    before = snapshot_tree(tmp_path)
    result = run_cli(["doctor", str(tmp_path)])
    assert result.exit_code == 0, result.stderr
    assert snapshot_tree(tmp_path) == before
    for content in before.values():
        assert content is None or KEY.encode() not in content


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="needs POSIX permissions as non-root")
def test_doctor_never_opens_source_files(tmp_path: Path, transport) -> None:
    transport(ollama_healthy())
    make_project(tmp_path, {"main.py": "x = 1\n", "pkg/mod.py": "y = 2\n"})
    locked = [tmp_path / "main.py", tmp_path / "pkg"]
    for path in locked:
        path.chmod(0)
    try:
        result = run_cli(["doctor", str(tmp_path)])
    finally:
        for path in locked:
            path.chmod(stat.S_IRWXU)
    assert result.exit_code == 0, result.stderr


def test_doctor_and_config_show_overrides(tmp_path: Path, transport) -> None:
    transport(ollama_healthy(models=("llama3:latest",)))
    make_project(tmp_path, {"codewhy.toml": '[llm]\nmodel = "from-file"\n'})
    result = run_cli(["doctor", str(tmp_path), "--model", "llama3"])
    assert result.exit_code == 0, result.stderr
    assert "model: llama3" in result.stdout
    shown = run_cli(["config", "show", str(tmp_path), "--provider", "gemini", "--model", "gem-x"])
    assert shown.exit_code == 0
    for line in (
        'provider = "gemini"',
        'model = "gem-x"',
        'base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"',
        'api_key_env = "GEMINI_API_KEY"',
        'structured_output = "schema"',
        'reasoning_effort = "low"',
        "send_seed = false",
        "context_tokens = 32768",
        "max_output_tokens = 8192",
    ):
        assert line in shown.stdout.splitlines(), line


def test_scan_accepts_provider_and_model(tmp_path: Path) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n"})
    result = run_cli(["scan", str(tmp_path), "--provider", "gemini", "--model", "gem-x"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.startswith("Found 1 source files")
