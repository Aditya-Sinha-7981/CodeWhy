"""Ollama on its native API (`/api/chat`), so the context window is set per request.

Every request body comes from `OllamaProvider.chat_body`, which always sets
`options.num_ctx`: Ollama's built-in default context is small and would silently
truncate prompts. A request built anywhere else is a defect.
"""

from __future__ import annotations

import logging
import re

import httpx

from codewhy.config import LLMConfig
from codewhy.consent import loopback, url_host
from codewhy.llm.provider import (
    PREFLIGHT_TIMEOUT_SECONDS,
    AuthError,
    BadRequestError,
    MalformedOutput,
    ProviderError,
    Sleep,
    body_excerpt,
    extract_json_object,
    response_json,
    send_with_retries,
)

logger = logging.getLogger(__name__)

# `format` with a JSON schema (structured outputs) arrived in Ollama 0.5.0.
MIN_OLLAMA_VERSION = (0, 5, 0)
MIN_OLLAMA_VERSION_TEXT = "0.5.0"

_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION.match(text.strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def model_available(wanted: str, installed: set[str]) -> bool:
    """`installed` holds `name:tag` strings; a bare `wanted` name means `<name>:latest`."""
    if wanted in installed:
        return True
    return ":" not in wanted and f"{wanted}:latest" in installed


class OllamaProvider:
    # A local model can consume most of the machine's memory. Keep requests
    # serial so independent passes do not trigger concurrent model loads or
    # KV-cache allocations.
    supports_concurrency = False

    def __init__(self, llm: LLMConfig, *, client: httpx.Client, sleep: Sleep) -> None:
        self.host = llm.host.rstrip("/")
        self.model = llm.effective_model or ""
        self.context_tokens = llm.effective_context_tokens
        self.temperature = llm.temperature
        self.seed = llm.seed
        self.timeout = float(llm.timeout_seconds)
        self.max_retries = llm.max_transient_retries
        self.client = client
        self.sleep = sleep
        self.is_remote = not loopback(url_host(self.host))
        self.endpoint_label = self.host

    def chat_body(
        self, prompt: str, schema: dict, *, max_output_tokens: int, temperature: float | None = None
    ) -> dict:
        """The one place an /api/chat body is built (num_ctx is always present)."""
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": schema,
            "options": {
                "num_ctx": self.context_tokens,
                "temperature": self.temperature if temperature is None else temperature,
                "seed": self.seed,
                "num_predict": max_output_tokens,
            },
        }

    def generate_json(
        self, prompt: str, schema: dict, *, max_output_tokens: int, temperature: float | None = None
    ) -> dict:
        body = self.chat_body(prompt, schema, max_output_tokens=max_output_tokens, temperature=temperature)
        response = send_with_retries(
            lambda: self.client.post(f"{self.host}/api/chat", json=body, timeout=self.timeout),
            max_retries=self.max_retries,
            sleep=self.sleep,
            endpoint_label=f"Ollama at {self.host}",
            timeout_seconds=self.timeout,
        )
        self._raise_for_status(response)
        data = response_json(response, f"Ollama at {self.host}")
        message = data.get("message") if isinstance(data, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise MalformedOutput(f"Ollama at {self.host} returned no message content", raw=response.text)
        try:
            return extract_json_object(content)
        except ValueError:
            if data.get("done_reason") == "length":
                raise MalformedOutput(
                    f"the model's output was cut off at max_output_tokens ({max_output_tokens}) before the JSON "
                    "was complete; raise llm.max_output_tokens",
                    raw=content,
                ) from None
            raise MalformedOutput("the model's reply did not contain a JSON object", raw=content) from None

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        if status in (401, 403):
            raise AuthError(f"Ollama at {self.host} refused the request (HTTP {status}); check the proxy in front of it")
        if status == 404:
            raise BadRequestError(f"Model '{self.model}' is not available locally. Run: ollama pull {self.model}")
        raise BadRequestError(
            f"Ollama at {self.host} rejected the request (HTTP {status}): {body_excerpt(response)}. "
            f"codewhy needs Ollama {MIN_OLLAMA_VERSION_TEXT} or newer for structured output."
        )

    def preflight(self) -> None:
        version_response = self._preflight_get("/api/version")
        if version_response.status_code != 200:
            raise ProviderError(
                f"Something answered at {self.host} but it does not look like Ollama "
                f"(GET /api/version returned HTTP {version_response.status_code}). Check llm.host."
            )
        payload = response_json(version_response, f"Ollama at {self.host}")
        text = payload.get("version", "") if isinstance(payload, dict) else ""
        version = parse_version(text) if isinstance(text, str) else None
        if version is None:
            logger.warning("could not read the Ollama version at %s; skipping the version check", self.host)
        elif version < MIN_OLLAMA_VERSION:
            raise ProviderError(
                f"Ollama {text} at {self.host} is too old: codewhy needs Ollama {MIN_OLLAMA_VERSION_TEXT} or newer "
                "(structured output). Update it from ollama.com."
            )

        tags_response = self._preflight_get("/api/tags")
        if tags_response.status_code != 200:
            raise ProviderError(
                f"Ollama at {self.host} could not list its models (HTTP {tags_response.status_code}). "
                "Check that Ollama is running correctly (`ollama list`)."
            )
        tags = response_json(tags_response, f"Ollama at {self.host}")
        models = tags.get("models", []) if isinstance(tags, dict) else []
        installed = {
            value
            for entry in models
            if isinstance(entry, dict)
            for value in (entry.get("name"), entry.get("model"))
            if isinstance(value, str)
        }
        if not model_available(self.model, installed):
            raise ProviderError(f"Model '{self.model}' is not available locally. Run: ollama pull {self.model}")

    def _preflight_get(self, path: str) -> httpx.Response:
        try:
            return self.client.get(f"{self.host}{path}", timeout=PREFLIGHT_TIMEOUT_SECONDS)
        except httpx.UnsupportedProtocol:
            raise ProviderError(f"llm.host {self.host!r} is not a valid URL; use e.g. http://localhost:11434") from None
        except httpx.TimeoutException:
            raise ProviderError(
                f"Timed out contacting Ollama at {self.host} after {PREFLIGHT_TIMEOUT_SECONDS:g} seconds. "
                "The server may be overloaded or still loading a model; check `ollama ps` and try again."
            ) from None
        except httpx.TransportError:
            raise ProviderError(
                f"Cannot reach Ollama at {self.host}. Start it with `ollama serve` (or install from ollama.com)."
            ) from None

    def close(self) -> None:
        self.client.close()
