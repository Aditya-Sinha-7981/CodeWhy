from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from codewhy.config import Config
from codewhy.llm.ollama_provider import OllamaProvider, model_available, parse_version
from codewhy.llm.provider import BadRequestError, MalformedOutput, ProviderError, TransientError, build_provider
from codewhy.summarize.schemas import FilePass, provider_schema

Handler = Callable[[httpx.Request], httpx.Response]
SCHEMA = provider_schema(FilePass)


def make(handler: Handler, **llm: object) -> tuple[OllamaProvider, list[float]]:
    sleeps: list[float] = []
    config = Config.model_validate({"llm": {"provider": "ollama", **llm}})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = build_provider(config, sleep=sleeps.append, client=client)
    assert isinstance(provider, OllamaProvider)
    return provider, sleeps


def chat_reply(content: str, done_reason: str = "stop") -> httpx.Response:
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content}, "done": True, "done_reason": done_reason})


def healthy(version: str = "0.34.4", models: tuple[str, ...] = ("qwen2.5-coder:14b",)) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": version})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m, "model": m} for m in models]})
        return chat_reply('{"purpose": "p"}')

    return handler


# --- request body -----------------------------------------------------------------


def test_context_window_num_ctx_is_sent_on_every_request() -> None:
    """Named regression (docs/TESTS.md): every /api/chat body carries options.num_ctx == context_tokens."""
    for context_tokens, expected in ((None, 16384), (4096, 4096), (65536, 65536)):
        bodies: list[dict] = []
        replies = iter(
            [httpx.Response(503), chat_reply('{"purpose": "a"}'), chat_reply('{"purpose": "b"}'), chat_reply("not json")]
        )

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return next(replies)

        overrides = {} if context_tokens is None else {"context_tokens": context_tokens}
        provider, _ = make(handler, **overrides)
        provider.generate_json("p1", SCHEMA, max_output_tokens=100)  # retried once after the 503
        provider.generate_json("p2", SCHEMA, max_output_tokens=200, temperature=0.4)
        with pytest.raises(MalformedOutput):
            provider.generate_json("p3", SCHEMA, max_output_tokens=300)
        direct = provider.chat_body("p4", SCHEMA, max_output_tokens=10)

        assert len(bodies) == 4
        for body in [*bodies, direct]:
            assert body["options"]["num_ctx"] == expected


def test_request_body_carries_format_temperature_seed_and_num_predict() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return chat_reply('{"purpose": "p"}')

    provider, _ = make(handler, temperature=0.2, seed=7, model="m:1")
    provider.generate_json("hello", SCHEMA, max_output_tokens=321)
    provider.generate_json("hello", SCHEMA, max_output_tokens=5, temperature=0.9)
    first, second = bodies
    assert first == {
        "model": "m:1",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
        "format": SCHEMA,
        "options": {"num_ctx": 16384, "temperature": 0.2, "seed": 7, "num_predict": 321},
    }
    assert second["options"]["temperature"] == 0.9
    assert second["options"]["num_predict"] == 5


def test_reply_is_parsed_to_a_dict() -> None:
    provider, _ = make(lambda r: chat_reply('{"purpose": "Loads config.", "notes": null}'))
    assert provider.generate_json("p", SCHEMA, max_output_tokens=10) == {"purpose": "Loads config.", "notes": None}


def test_non_json_content_is_malformed_output_with_truncated_raw_text() -> None:
    provider, _ = make(lambda r: chat_reply("Sure! " + "x" * 5000))
    with pytest.raises(MalformedOutput) as info:
        provider.generate_json("p", SCHEMA, max_output_tokens=10)
    assert info.value.raw.startswith("Sure! ")
    assert len(info.value.raw) == 2000


def test_cut_off_output_mentions_max_output_tokens() -> None:
    provider, _ = make(lambda r: chat_reply('{"purpose": "unfinish', done_reason="length"))
    with pytest.raises(MalformedOutput, match=r"cut off at max_output_tokens \(10\)"):
        provider.generate_json("p", SCHEMA, max_output_tokens=10)


def test_non_json_body_and_missing_content_are_malformed() -> None:
    provider, _ = make(lambda r: httpx.Response(200, text="<html>"))
    with pytest.raises(MalformedOutput, match="not JSON"):
        provider.generate_json("p", SCHEMA, max_output_tokens=10)
    provider, _ = make(lambda r: httpx.Response(200, json={"done": True}))
    with pytest.raises(MalformedOutput, match="no message content"):
        provider.generate_json("p", SCHEMA, max_output_tokens=10)


def test_missing_model_on_chat_says_how_to_pull_it() -> None:
    provider, _ = make(lambda r: httpx.Response(404, json={"error": "model 'x' not found"}), model="x")
    with pytest.raises(BadRequestError, match="ollama pull x"):
        provider.generate_json("p", SCHEMA, max_output_tokens=10)


# --- transient handling ---------------------------------------------------------------


def test_transient_errors_retry_with_backoff_and_stop_at_the_limit() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(500)

    provider, sleeps = make(handler, max_transient_retries=3)
    with pytest.raises(TransientError, match="failed 4 time"):
        provider.generate_json("p", SCHEMA, max_output_tokens=10)
    assert len(attempts) == 4
    assert sleeps == [1.0, 2.0, 4.0]


def test_connection_errors_are_transient_and_recover() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ConnectError("refused", request=request)
        return chat_reply('{"purpose": "p"}')

    provider, sleeps = make(handler)
    assert provider.generate_json("p", SCHEMA, max_output_tokens=10) == {"purpose": "p"}
    assert sleeps == [1.0, 2.0]


def test_zero_retries_means_one_attempt() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    provider, sleeps = make(handler, max_transient_retries=0)
    with pytest.raises(TransientError):
        provider.generate_json("p", SCHEMA, max_output_tokens=10)
    assert sleeps == []


def test_generation_timeout_error_names_configured_duration() -> None:
    timeout_extensions: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeout_extensions.append(request.extensions["timeout"])
        raise httpx.ReadTimeout("slow", request=request)

    provider, _ = make(handler, timeout_seconds=4, max_transient_retries=0)
    with pytest.raises(TransientError, match=r"timed out after 4 seconds"):
        provider.generate_json("p", SCHEMA, max_output_tokens=10)
    assert timeout_extensions == [{"connect": 4.0, "read": 4.0, "write": 4.0, "pool": 4.0}]


# --- preflight -------------------------------------------------------------------------


def test_preflight_passes_when_healthy() -> None:
    provider, _ = make(healthy())
    provider.preflight()


def test_preflight_unreachable_host_says_ollama_serve() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    provider, _ = make(handler, host="http://localhost:11434/")
    with pytest.raises(ProviderError) as info:
        provider.preflight()
    assert str(info.value) == (
        "Cannot reach Ollama at http://localhost:11434. Start it with `ollama serve` (or install from ollama.com)."
    )


def test_preflight_timeout_error_names_fixed_duration() -> None:
    timeout_extensions: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeout_extensions.append(request.extensions["timeout"])
        raise httpx.ReadTimeout("slow", request=request)

    provider, _ = make(handler)
    with pytest.raises(ProviderError, match=r"Timed out contacting Ollama .* after 10 seconds"):
        provider.preflight()
    assert timeout_extensions == [{"connect": 10.0, "read": 10.0, "write": 10.0, "pool": 10.0}]


def test_ollama_does_not_opt_into_parallel_generation() -> None:
    provider, _ = make(healthy())
    assert provider.supports_concurrency is False


def test_preflight_missing_model_says_ollama_pull() -> None:
    provider, _ = make(healthy(models=("llama3:8b",)), model="qwen2.5-coder:7b")
    with pytest.raises(ProviderError) as info:
        provider.preflight()
    assert str(info.value) == "Model 'qwen2.5-coder:7b' is not available locally. Run: ollama pull qwen2.5-coder:7b"


def test_preflight_default_model_is_the_architecture_default() -> None:
    provider, _ = make(healthy(models=("other:latest",)))
    with pytest.raises(ProviderError, match="ollama pull qwen2.5-coder:14b"):
        provider.preflight()


def test_preflight_old_version_names_the_requirement() -> None:
    provider, _ = make(healthy(version="0.4.7"))
    with pytest.raises(ProviderError, match=r"Ollama 0\.4\.7 .* needs Ollama 0\.5\.0 or newer"):
        provider.preflight()


def test_preflight_unknown_version_string_is_not_fatal() -> None:
    provider, _ = make(healthy(version="dev-build"))
    provider.preflight()


def test_preflight_non_ollama_server_is_reported() -> None:
    provider, _ = make(lambda r: httpx.Response(404, text="nope"))
    with pytest.raises(ProviderError, match="does not look like Ollama"):
        provider.preflight()


def test_preflight_bad_host_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.UnsupportedProtocol("no scheme", request=request)

    provider, _ = make(handler)
    with pytest.raises(ProviderError, match="not a valid URL"):
        provider.preflight()


@pytest.mark.parametrize(
    ("wanted", "installed", "expected"),
    [
        ("llama3", {"llama3:latest"}, True),
        ("llama3", {"llama3:8b"}, False),
        ("llama3:8b", {"llama3:8b"}, True),
        ("llama3:latest", {"llama3:latest"}, True),
        ("llama3:8b", {"llama3:latest"}, False),
    ],
)
def test_bare_model_name_matches_latest_tag(wanted: str, installed: set[str], expected: bool) -> None:
    assert model_available(wanted, installed) is expected


def test_bare_model_name_passes_preflight_against_latest() -> None:
    provider, _ = make(healthy(models=("llama3:latest",)), model="llama3")
    provider.preflight()


def test_parse_version() -> None:
    assert parse_version("0.34.4") == (0, 34, 4)
    assert parse_version("v0.5.0-rc1") == (0, 5, 0)
    assert parse_version("0.0") is None


# --- local vs remote --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "remote"),
    [
        ("http://localhost:11434", False),
        ("http://127.0.0.1:11434", False),
        ("http://[::1]:11434", False),
        ("http://LOCALHOST:11434", False),
        ("http://192.168.1.20:11434", True),
        ("https://ollama.example.com", True),
    ],
)
def test_loopback_hosts_are_local(host: str, remote: bool) -> None:
    provider, _ = make(healthy(), host=host)
    assert provider.is_remote is remote
    assert provider.endpoint_label == host
