"""The provider interface, its errors, and the HTTP plumbing both providers share.

docs/ARCHITECTURE.md -> Provider interface. Nothing outside `codewhy.llm` should
import a concrete provider; use `build_provider` and the `Provider` protocol.

Logging rule: nothing here logs prompts, responses, source text, or API keys.
Retry decisions are logged at debug level with status codes only.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx

from codewhy.config import Config, ConfigError, apply_preset

logger = logging.getLogger(__name__)

RAW_EXCERPT_CHARS = 2000
BODY_EXCERPT_CHARS = 300
BACKOFF_CAP_SECONDS = 30.0
RETRY_AFTER_CAP_SECONDS = 60.0
# Preflight should fail fast; the configured timeout_seconds is for generation.
PREFLIGHT_TIMEOUT_SECONDS = 10.0

Sleep = Callable[[float], None]


class Provider(Protocol):
    is_remote: bool
    endpoint_label: str

    def preflight(self) -> None: ...

    def generate_json(
        self, prompt: str, schema: dict, *, max_output_tokens: int, temperature: float | None = None
    ) -> dict: ...


class ProviderError(Exception):
    """A provider failure; the message is user-presentable and says how to fix it."""


class AuthError(ProviderError):
    """Missing or rejected credentials. Never retried."""


class TransientError(ProviderError):
    """Network trouble, 429 or 5xx; raised only after the retries are exhausted."""


class BadRequestError(ProviderError):
    """The endpoint rejected the request (a 4xx other than 401/403/429)."""


class MalformedOutput(ProviderError):
    """The response could not be parsed as a JSON object. The pass layer decides whether to retry."""

    def __init__(self, message: str, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw[:RAW_EXCERPT_CHARS]


def build_provider(config: Config, *, sleep: Sleep = time.sleep, client: httpx.Client | None = None) -> Provider:
    """The provider for `config.llm`, presets applied. Raises ConfigError for unusable settings."""
    # Concrete providers import this module, so they are imported here, not at the top.
    from codewhy.llm.ollama_provider import OllamaProvider
    from codewhy.llm.openai_compat_provider import OpenAICompatProvider

    llm = apply_preset(config).llm
    http = client if client is not None else httpx.Client()
    if llm.provider == "ollama":
        return OllamaProvider(llm, client=http, sleep=sleep)
    if llm.provider in ("openai-compat", "gemini"):
        if not llm.model:
            raise ConfigError(
                [f"llm.model is required for provider '{llm.provider}'; model ids change, so codewhy does not ship a default"]
            )
        if not llm.base_url:
            raise ConfigError([f"llm.base_url is required for provider '{llm.provider}' (e.g. https://<host>/v1)"])
        return OpenAICompatProvider(llm, client=http, sleep=sleep)
    raise ConfigError([f"unknown llm.provider {llm.provider!r}; expected 'ollama', 'openai-compat' or 'gemini'"])


# --- HTTP plumbing ------------------------------------------------------------


def is_transient_status(status: int) -> bool:
    return status == 429 or 500 <= status <= 599


def backoff_delay(retry_number: int, retry_after: float | None) -> float:
    """1s, 2s, 4s, ... capped at 30s; a Retry-After header (capped at 60s) wins when longer."""
    delay = min(2.0**retry_number, BACKOFF_CAP_SECONDS)
    if retry_after is not None:
        delay = max(delay, min(retry_after, RETRY_AFTER_CAP_SECONDS))
    return delay


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds from a Retry-After header (delta-seconds or HTTP-date); None if absent or unparseable."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return max((when - current).total_seconds(), 0.0)


def send_with_retries(
    send: Callable[[], httpx.Response],
    *,
    max_retries: int,
    sleep: Sleep,
    endpoint_label: str,
) -> httpx.Response:
    """Run `send`, retrying transient failures; return the first non-transient response.

    Status mapping beyond "transient" (401/403/other 4xx) is the caller's job, so
    each provider can phrase the fix for its own endpoint.
    """
    retry_number = 0
    while True:
        retry_after: float | None = None
        try:
            response = send()
        except httpx.UnsupportedProtocol as exc:
            raise ProviderError(
                f"invalid endpoint URL for {endpoint_label} ({exc}); it must start with http:// or https://"
            ) from None
        except httpx.TransportError as exc:  # connection errors and timeouts
            last = f"{type(exc).__name__}"
        else:
            if not is_transient_status(response.status_code):
                return response
            last = f"HTTP {response.status_code}"
            retry_after = parse_retry_after(response.headers.get("retry-after"))
        if retry_number >= max_retries:
            raise TransientError(
                f"{endpoint_label} failed {retry_number + 1} time(s) in a row (last error: {last}). "
                "This is usually temporary (network trouble, rate limit, or server load): wait and run again, "
                "or raise llm.max_transient_retries."
            )
        delay = backoff_delay(retry_number, retry_after)
        logger.debug("transient failure from %s (%s); retry %d in %.1fs", endpoint_label, last, retry_number + 1, delay)
        sleep(delay)
        retry_number += 1


_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")


def redact(text: str, secrets: Iterable[str | None] = ()) -> str:
    """`text` with every secret and any bearer token replaced by [redacted]."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return _BEARER.sub(r"\1[redacted]", text)


def body_excerpt(response: httpx.Response, secrets: Iterable[str | None] = ()) -> str:
    """A short, single-line, key-redacted excerpt of an error response body."""
    try:
        text = response.text
    except (UnicodeDecodeError, httpx.HTTPError):
        return "(unreadable body)"
    text = " ".join(redact(text, secrets).split())
    if len(text) > BODY_EXCERPT_CHARS:
        text = text[:BODY_EXCERPT_CHARS] + "…"
    return text or "(empty body)"


def extract_json_object(text: str) -> dict[str, Any]:
    """The first JSON object in `text`, tolerating Markdown fences and surrounding prose.

    Raises ValueError when there is none.
    """
    stripped = text.strip()
    try:
        value = json.loads(stripped)
    except ValueError:
        pass
    else:
        if isinstance(value, dict):
            return value
    decoder = json.JSONDecoder()
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text, start)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("no JSON object found")


def response_json(response: httpx.Response, endpoint_label: str) -> Any:
    """The decoded JSON body of a successful response, or MalformedOutput."""
    try:
        return response.json()
    except ValueError:
        raise MalformedOutput(
            f"{endpoint_label} returned a response body that is not JSON; check that the URL points at the API",
            raw=response.text,
        ) from None
