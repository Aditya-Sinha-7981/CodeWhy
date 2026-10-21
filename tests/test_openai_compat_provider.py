from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from codewhy.config import GEMINI_BASE_URL, Config, ConfigError, apply_preset, resolve_config
from codewhy.llm.openai_compat_provider import OpenAICompatProvider
from codewhy.llm.provider import (
    AuthError,
    BadRequestError,
    MalformedOutput,
    ProviderError,
    TransientError,
    build_provider,
    extract_json_object,
    parse_retry_after,
)
from codewhy.summarize.schemas import FilePass, provider_schema

Handler = Callable[[httpx.Request], httpx.Response]
SCHEMA = provider_schema(FilePass)
KEY = "sk-test-SECRET-9f8e7d6c5b4a"


@pytest.fixture(autouse=True)
def api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setenv("CODEWHY_API_KEY", KEY)


def make(handler: Handler, **llm: object) -> tuple[OpenAICompatProvider, list[float]]:
    sleeps: list[float] = []
    settings = {"provider": "gemini", "model": "gemini-test", **llm}
    config = apply_preset(Config.model_validate({"llm": settings}))
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = build_provider(config, sleep=sleeps.append, client=client)
    assert isinstance(provider, OpenAICompatProvider)
    return provider, sleeps


def completion(content: str | None, finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}]}
    )


def recording(bodies: list[dict], reply: httpx.Response | None = None) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return reply or completion('{"purpose": "p"}')

    return handler


# --- preset application -----------------------------------------------------------


def test_gemini_preset_fills_every_default(tmp_path: Path) -> None:
    llm = resolve_config(tmp_path, {"llm.provider": "gemini", "llm.model": "m"}).llm
    assert llm.base_url == GEMINI_BASE_URL == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert (llm.api_key_env, llm.structured_output, llm.reasoning_effort, llm.send_seed) == (
        "GEMINI_API_KEY",
        "schema",
        "low",
        False,
    )
    assert (llm.context_tokens, llm.max_output_tokens) == (32768, 8192)


def test_user_values_win_over_the_preset(tmp_path: Path) -> None:
    (tmp_path / "codewhy.toml").write_text(
        '[llm]\nprovider = "gemini"\nmodel = "m"\nbase_url = "https://proxy.example.com/v1"\n'
        'api_key_env = "MY_KEY"\nstructured_output = "json"\nreasoning_effort = "medium"\nsend_seed = true\n'
        "context_tokens = 100000\nmax_output_tokens = 2048\n"
    )
    llm = resolve_config(tmp_path).llm
    assert (llm.base_url, llm.api_key_env, llm.structured_output, llm.reasoning_effort, llm.send_seed) == (
        "https://proxy.example.com/v1",
        "MY_KEY",
        "json",
        "medium",
        True,
    )
    assert (llm.context_tokens, llm.max_output_tokens) == (100000, 2048)


def test_preset_applies_only_to_gemini(tmp_path: Path) -> None:
    llm = resolve_config(tmp_path, {"llm.provider": "openai-compat"}).llm
    assert (llm.base_url, llm.api_key_env, llm.structured_output, llm.reasoning_effort) == (None, None, None, None)
    assert (llm.effective_context_tokens, llm.effective_max_output_tokens) == (32768, 1024)


@pytest.mark.parametrize("provider", ["gemini", "openai-compat"])
def test_missing_model_is_a_clear_config_error(tmp_path: Path, provider: str) -> None:
    config = resolve_config(tmp_path, {"llm.provider": provider, "llm.base_url": "https://x.example.com/v1"})
    with pytest.raises(ConfigError) as info:
        build_provider(config)
    assert str(info.value) == (
        f"invalid configuration: llm.model is required for provider '{provider}'; "
        "model ids change, so codewhy does not ship a default"
    )


def test_openai_compat_needs_a_base_url(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="llm.base_url is required"):
        build_provider(resolve_config(tmp_path, {"llm.provider": "openai-compat", "llm.model": "m"}))


def test_no_hosted_default_model_exists_in_the_code() -> None:
    source = Path(__file__).resolve().parents[1] / "src" / "codewhy"
    for path in source.rglob("*.py"):
        assert "gemini-" not in path.read_text(encoding="utf-8"), path


# --- request body ---------------------------------------------------------------------


def test_body_for_schema_mode_sends_json_schema() -> None:
    bodies: list[dict] = []
    provider, _ = make(recording(bodies))
    assert provider.generate_json("hello", SCHEMA, max_output_tokens=500) == {"purpose": "p"}
    assert bodies == [
        {
            "model": "gemini-test",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.0,
            "max_tokens": 500,
            "reasoning_effort": "low",
            "response_format": {"type": "json_schema", "json_schema": {"name": "codewhy_result", "schema": SCHEMA}},
        }
    ]


@pytest.mark.parametrize(("mode", "expected"), [("json", {"type": "json_object"}), ("prompt", None)])
def test_body_for_json_and_prompt_modes(mode: str, expected: dict | None) -> None:
    bodies: list[dict] = []
    provider, _ = make(recording(bodies), structured_output=mode)
    provider.generate_json("hello", SCHEMA, max_output_tokens=500)
    assert bodies[0].get("response_format") == expected


def test_unset_structured_output_on_openai_compat_means_prompt() -> None:
    bodies: list[dict] = []
    provider, _ = make(recording(bodies), provider="openai-compat", base_url="https://api.example.com/v1")
    provider.generate_json("hello", SCHEMA, max_output_tokens=500)
    assert "response_format" not in bodies[0]
    assert "reasoning_effort" not in bodies[0]
    assert "seed" not in bodies[0]


def test_seed_only_with_send_seed_and_temperature_override() -> None:
    bodies: list[dict] = []
    provider, _ = make(recording(bodies), send_seed=True, seed=42, temperature=0.1)
    provider.generate_json("a", SCHEMA, max_output_tokens=10)
    provider.generate_json("b", SCHEMA, max_output_tokens=10, temperature=0.7)
    assert [b["seed"] for b in bodies] == [42, 42]
    assert [b["temperature"] for b in bodies] == [0.1, 0.7]
    assert all("max_tokens" in b for b in bodies)


def test_reasoning_effort_absent_when_unset() -> None:
    bodies: list[dict] = []
    provider, _ = make(recording(bodies), provider="openai-compat", base_url="https://api.example.com/v1", reasoning_effort=None)
    provider.generate_json("a", SCHEMA, max_output_tokens=10)
    assert "reasoning_effort" not in bodies[0]


def test_url_and_label() -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return completion('{"purpose": "p"}')

    provider, _ = make(handler)
    provider.generate_json("a", SCHEMA, max_output_tokens=10)
    assert urls == ["https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"]
    assert provider.endpoint_label == "generativelanguage.googleapis.com"
    assert provider.is_remote is True
    local, _ = make(handler, provider="openai-compat", base_url="http://localhost:1234/v1/")
    assert (local.endpoint_label, local.is_remote) == ("localhost:1234", True)


# --- API key ---------------------------------------------------------------------------


def test_key_is_read_from_the_environment_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers.get("authorization"))
        return completion('{"purpose": "p"}')

    provider, _ = make(handler)
    monkeypatch.setenv("GEMINI_API_KEY", "rotated-key")
    provider.generate_json("a", SCHEMA, max_output_tokens=10)
    assert headers == ["Bearer rotated-key"]


def test_missing_key_names_the_variable_before_any_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY")
    calls: list[httpx.Request] = []
    provider, _ = make(lambda r: calls.append(r) or completion("{}"))
    for action in (lambda: provider.generate_json("a", SCHEMA, max_output_tokens=10), provider.preflight):
        with pytest.raises(AuthError, match="The environment variable GEMINI_API_KEY is not set"):
            action()
    assert calls == []


def test_no_api_key_env_sends_no_authorization_header() -> None:
    headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers.get("authorization"))
        return completion('{"purpose": "p"}')

    provider, _ = make(handler, provider="openai-compat", base_url="http://localhost:1234/v1")
    provider.generate_json("a", SCHEMA, max_output_tokens=10)
    assert headers == [None]


def test_the_key_never_leaks_into_errors_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    echo = f'{{"error": {{"message": "bad request with Authorization: Bearer {KEY} and key={KEY}"}}}}'
    responses: list[Handler] = [
        lambda r: httpx.Response(401, text=echo),
        lambda r: httpx.Response(400, text=echo),
        lambda r: httpx.Response(422, text=echo),
        lambda r: httpx.Response(500, text=echo),
        lambda r: httpx.Response(200, text=echo),
        lambda r: completion(None, finish_reason="length"),
    ]
    messages: list[str] = []
    for handler in responses:
        provider, _ = make(handler, max_transient_retries=1)
        for action in (lambda: provider.generate_json("a", SCHEMA, max_output_tokens=10), provider.preflight):
            try:
                action()
            except ProviderError as exc:
                messages.append(str(exc))
    assert messages
    assert any("[redacted]" in m for m in messages)
    for text in [*messages, caplog.text]:
        assert KEY not in text


# --- error mapping ----------------------------------------------------------------------


def test_401_is_auth_error_and_never_retried() -> None:
    calls: list[int] = []
    provider, sleeps = make(lambda r: calls.append(1) or httpx.Response(401))
    with pytest.raises(AuthError, match="rejected the key in GEMINI_API_KEY"):
        provider.generate_json("a", SCHEMA, max_output_tokens=10)
    assert (calls, sleeps) == ([1], [])


def test_gemini_invalid_key_400_is_an_auth_error() -> None:
    body = '[{"error": {"code": 400, "message": "Please pass a valid API key", "status": "INVALID_ARGUMENT"}}]'
    provider, _ = make(lambda r: httpx.Response(400, text=body))
    with pytest.raises(AuthError):
        provider.generate_json("a", SCHEMA, max_output_tokens=10)
    with pytest.raises(AuthError):
        provider.preflight()


def test_429_honors_retry_after_and_backoff_stops_at_the_limit() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "7"})

    provider, sleeps = make(handler, max_transient_retries=2)
    with pytest.raises(TransientError, match="failed 3 time"):
        provider.generate_json("a", SCHEMA, max_output_tokens=10)
    assert len(calls) == 3
    assert sleeps == [7.0, 7.0]


def test_retry_after_is_capped_and_backoff_is_capped() -> None:
    replies = iter([httpx.Response(503, headers={"Retry-After": "600"})] + [httpx.Response(503)] * 6 + [completion('{"purpose": "p"}')])
    provider, sleeps = make(lambda r: next(replies), max_transient_retries=7)
    assert provider.generate_json("a", SCHEMA, max_output_tokens=10) == {"purpose": "p"}
    assert sleeps == [60.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]


def test_retry_after_http_date() -> None:
    from datetime import datetime, timezone

    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
    assert parse_retry_after("Tue, 20 Oct 2026 12:00:05 GMT", now=now) == 5.0
    assert parse_retry_after("garbage") is None
    assert parse_retry_after(None) is None


def test_400_in_schema_mode_suggests_another_structured_output_mode() -> None:
    provider, _ = make(lambda r: httpx.Response(400, json={"error": {"message": "Invalid JSON payload: response_format"}}))
    with pytest.raises(BadRequestError) as info:
        provider.generate_json("a", SCHEMA, max_output_tokens=10)
    message = str(info.value)
    assert 'llm.structured_output = "json"' in message
    assert "Invalid JSON payload" in message


def test_402_points_at_billing_not_config_and_is_not_retried() -> None:
    body = '[{"error": {"code": 402, "message": "Your prepayment credits are depleted.", "status": "RESOURCE_EXHAUSTED"}}]'
    calls: list[int] = []
    provider, sleeps = make(lambda r: calls.append(1) or httpx.Response(402, text=body))
    with pytest.raises(BadRequestError) as info:
        provider.generate_json("a", SCHEMA, max_output_tokens=10)
    message = str(info.value)
    assert "billing" in message and "credits are depleted" in message
    assert "llm.model" not in message
    assert (calls, sleeps) == ([1], [])


def test_other_4xx_is_bad_request_with_excerpt() -> None:
    provider, _ = make(lambda r: httpx.Response(404, text="model not found " + "z" * 1000), structured_output="json")
    with pytest.raises(BadRequestError) as info:
        provider.generate_json("a", SCHEMA, max_output_tokens=10)
    assert "HTTP 404" in str(info.value) and "model not found" in str(info.value)
    assert len(str(info.value)) < 600


# --- parsing --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        '{"purpose": "p"}',
        '```json\n{"purpose": "p"}\n```',
        'Here is the JSON you asked for:\n{"purpose": "p"}\nHope that helps!',
        'Note {not json} then {"purpose": "p"}',
    ],
)
def test_json_is_extracted_from_fences_and_prose(content: str) -> None:
    provider, _ = make(lambda r: completion(content), structured_output="prompt")
    assert provider.generate_json("a", SCHEMA, max_output_tokens=10) == {"purpose": "p"}


def test_nested_braces_and_strings_are_handled() -> None:
    assert extract_json_object('x {"a": "}{", "b": {"c": [1, {"d": 2}]}} y') == {"a": "}{", "b": {"c": [1, {"d": 2}]}}
    with pytest.raises(ValueError):
        extract_json_object("[1, 2]")


@pytest.mark.parametrize(
    "reply",
    [
        completion('{"purpose": "cut', finish_reason="length"),
        completion(None, finish_reason="length"),
        completion(""),
        httpx.Response(200, json={"choices": []}),
    ],
)
def test_truncated_or_empty_output_mentions_truncation(reply: httpx.Response) -> None:
    provider, _ = make(lambda r: reply)
    with pytest.raises(MalformedOutput) as info:
        provider.generate_json("a", SCHEMA, max_output_tokens=1024)
    assert "cut off at max_output_tokens (1024)" in str(info.value)
    assert "reasoning" in str(info.value)


def test_prose_without_json_is_malformed() -> None:
    provider, _ = make(lambda r: completion("I cannot help with that."))
    with pytest.raises(MalformedOutput, match="did not contain a JSON object") as info:
        provider.generate_json("a", SCHEMA, max_output_tokens=10)
    assert info.value.raw == "I cannot help with that."


# --- preflight -----------------------------------------------------------------------------


def models_listing(*ids: str) -> httpx.Response:
    return httpx.Response(200, json={"object": "list", "data": [{"id": i, "object": "model"} for i in ids]})


def test_preflight_ok_with_model_listed(caplog: pytest.LogCaptureFixture) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url}")
        return models_listing("models/gemini-test", "other")

    provider, _ = make(handler)
    provider.preflight()
    assert seen == ["GET https://generativelanguage.googleapis.com/v1beta/openai/models"]
    assert "not in the model list" not in caplog.text


def test_preflight_model_missing_is_only_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    provider, _ = make(lambda r: models_listing("something-else"))
    provider.preflight()
    assert "model 'gemini-test' is not in the model list" in caplog.text


@pytest.mark.parametrize("status", [401, 403])
def test_preflight_auth_failure(status: int) -> None:
    provider, _ = make(lambda r: httpx.Response(status))
    with pytest.raises(AuthError, match=f"rejected the key in GEMINI_API_KEY \\(HTTP {status}\\)"):
        provider.preflight()


@pytest.mark.parametrize("status", [404, 405, 501])
def test_preflight_skips_endpoints_without_a_listing(status: int, caplog: pytest.LogCaptureFixture) -> None:
    provider, _ = make(lambda r: httpx.Response(status))
    provider.preflight()
    assert "WARNING" not in caplog.text


def test_preflight_connection_error_is_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failure", request=request)

    provider, _ = make(handler)
    with pytest.raises(ProviderError, match="Cannot reach generativelanguage.googleapis.com .* Check llm.base_url"):
        provider.preflight()


def test_preflight_rate_limit_does_not_block(caplog: pytest.LogCaptureFixture) -> None:
    provider, _ = make(lambda r: httpx.Response(429))
    provider.preflight()
    assert "HTTP 429" in caplog.text
