"""Any OpenAI-style `/chat/completions` endpoint; the `gemini` preset points it at Google.

The API key is read from the environment variable named by `llm.api_key_env`
when a request is made, used only in the Authorization header, and never
logged, stored, or included in an error message.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit

import httpx

from codewhy.config import LLMConfig
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

# json_schema needs a name; it is not shown to the model as an instruction.
RESPONSE_SCHEMA_NAME = "codewhy_result"
# Statuses meaning "this endpoint has no model listing": skip that check.
NO_LISTING_STATUSES = (404, 405, 501)
# Gemini answers an invalid key with HTTP 400 rather than 401; its body says
# "Please pass a valid API key" (observed live 2026-10-20) or "API key not valid".
_INVALID_KEY_MARKERS = ("API_KEY_INVALID", "API key not valid", "valid API key")


def _label(base_url: str) -> str:
    parts = urlsplit(base_url)
    host = parts.hostname or base_url
    return f"{host}:{parts.port}" if parts.port else host


class OpenAICompatProvider:
    def __init__(self, llm: LLMConfig, *, client: httpx.Client, sleep: Sleep) -> None:
        self.provider_name = llm.provider
        self.base_url = (llm.base_url or "").rstrip("/")
        self.model = llm.model or ""
        self.api_key_env = llm.api_key_env
        # Unset means the most compatible behavior: no response_format, no seed.
        self.structured_output = llm.structured_output or "prompt"
        self.send_seed = bool(llm.send_seed)
        self.reasoning_effort = llm.reasoning_effort
        self.temperature = llm.temperature
        self.seed = llm.seed
        self.timeout = float(llm.timeout_seconds)
        self.max_retries = llm.max_transient_retries
        self.client = client
        self.sleep = sleep
        # Every openai-compat endpoint counts as remote, even on localhost
        # (docs/ARCHITECTURE.md -> Privacy: only ollama on loopback is local).
        self.is_remote = True
        self.endpoint_label = _label(self.base_url)

    # --- request building -------------------------------------------------

    def request_body(
        self, prompt: str, schema: dict, *, max_output_tokens: int, temperature: float | None = None
    ) -> dict:
        """The one place a /chat/completions body is built."""
        body: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": max_output_tokens,
        }
        if self.send_seed:
            body["seed"] = self.seed
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        if self.structured_output == "schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": RESPONSE_SCHEMA_NAME, "schema": schema},
            }
        elif self.structured_output == "json":
            body["response_format"] = {"type": "json_object"}
        return body

    def _api_key(self) -> str | None:
        """The key, read now from the environment; None when no key variable is configured."""
        if not self.api_key_env:
            return None
        key = os.environ.get(self.api_key_env, "")
        if not key.strip():
            raise AuthError(
                f"The environment variable {self.api_key_env} is not set. Set it to your API key for "
                f"{self.endpoint_label} in your shell (llm.api_key_env names the variable; the key itself "
                "never goes in codewhy.toml)."
            )
        return key.strip()

    def _headers(self, key: str | None) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}"} if key else {}

    # --- generation -------------------------------------------------------

    def generate_json(
        self, prompt: str, schema: dict, *, max_output_tokens: int, temperature: float | None = None
    ) -> dict:
        key = self._api_key()
        body = self.request_body(prompt, schema, max_output_tokens=max_output_tokens, temperature=temperature)
        headers = self._headers(key)
        response = send_with_retries(
            lambda: self.client.post(f"{self.base_url}/chat/completions", json=body, headers=headers, timeout=self.timeout),
            max_retries=self.max_retries,
            sleep=self.sleep,
            endpoint_label=self.endpoint_label,
            timeout_seconds=self.timeout,
        )
        self._raise_for_status(response, key)
        data = response_json(response, self.endpoint_label)
        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices or not isinstance(choices[0], dict):
            raise MalformedOutput(self._truncation_message(max_output_tokens, "returned no choices"), raw=response.text)
        choice = choices[0]
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        try:
            if not isinstance(content, str):
                raise ValueError("no content")
            return extract_json_object(content)
        except ValueError:
            raw = content if isinstance(content, str) else response.text
            if choice.get("finish_reason") == "length" or not content:
                raise MalformedOutput(self._truncation_message(max_output_tokens, "returned truncated or empty output"), raw=raw) from None
            raise MalformedOutput(f"the reply from {self.endpoint_label} did not contain a JSON object", raw=raw) from None

    def _truncation_message(self, max_output_tokens: int, what: str) -> str:
        return (
            f"{self.endpoint_label} {what}: the reply was probably cut off at max_output_tokens "
            f"({max_output_tokens}). Thinking models count reasoning tokens against that cap; raise "
            "llm.max_output_tokens or lower llm.reasoning_effort."
        )

    def _raise_for_status(self, response: httpx.Response, key: str | None) -> None:
        status = response.status_code
        if status < 400:
            return
        excerpt = body_excerpt(response, [key])
        if status in (401, 403) or any(marker in excerpt for marker in _INVALID_KEY_MARKERS):
            raise AuthError(self._auth_message(status))
        if status == 402:
            # Billing, not the request: Gemini sends this when prepaid credits run out
            # (observed live 2026-10-21). Retrying cannot help.
            raise BadRequestError(
                f"{self.endpoint_label} refused the request for billing reasons (HTTP 402): {excerpt}. "
                "Check the credits or billing for the account behind the key; nothing is wrong with codewhy.toml."
            )
        if status == 400 and self.structured_output == "schema":
            raise BadRequestError(
                f"{self.endpoint_label} rejected the request (HTTP 400): {excerpt}. If it does not support JSON "
                'schemas, set llm.structured_output = "json" (or "prompt") in codewhy.toml.'
            )
        raise BadRequestError(
            f"{self.endpoint_label} rejected the request (HTTP {status}): {excerpt}. Check llm.model and the "
            "optional parameters (structured_output, send_seed, reasoning_effort)."
        )

    def _auth_message(self, status: int) -> str:
        source = f"the key in {self.api_key_env}" if self.api_key_env else "the request (no llm.api_key_env is set)"
        return (
            f"{self.endpoint_label} rejected {source} (HTTP {status}). Check that the key is valid and "
            f"has access to model '{self.model}'."
        )

    # --- preflight ----------------------------------------------------------

    def preflight(self) -> None:
        key = self._api_key()
        try:
            response = self.client.get(
                f"{self.base_url}/models",
                headers=self._headers(key),
                timeout=min(self.timeout, PREFLIGHT_TIMEOUT_SECONDS),
            )
        except httpx.UnsupportedProtocol:
            raise ProviderError(f"llm.base_url {self.base_url!r} is not a valid URL; it must start with https://") from None
        except httpx.TimeoutException:
            raise ProviderError(
                f"Timed out contacting {self.endpoint_label} after {min(self.timeout, PREFLIGHT_TIMEOUT_SECONDS):g} seconds. "
                f"Check {self.base_url} and the provider status."
            ) from None
        except httpx.TransportError:
            raise ProviderError(
                f"Cannot reach {self.endpoint_label} ({self.base_url}). Check llm.base_url and your network connection."
            ) from None
        status = response.status_code
        if status in NO_LISTING_STATUSES:
            logger.debug("%s has no model listing (HTTP %d); skipping the model check", self.endpoint_label, status)
            return
        if status in (401, 403) or (400 <= status < 500 and any(m in body_excerpt(response, [key]) for m in _INVALID_KEY_MARKERS)):
            raise AuthError(self._auth_message(status))
        if status >= 400:
            # 429 / 5xx / odd 4xx on the listing alone should not block a run.
            logger.warning(
                "%s returned HTTP %d for the model list; skipping the model check", self.endpoint_label, status
            )
            return
        listing = response_json(response, self.endpoint_label)
        entries = listing.get("data", []) if isinstance(listing, dict) else []
        ids = {entry.get("id") for entry in entries if isinstance(entry, dict) and isinstance(entry.get("id"), str)}
        if self.model not in ids and not any(i.endswith(f"/{self.model}") for i in ids if i):
            logger.warning(
                "model '%s' is not in the model list from %s; continuing (ids may be listed differently)",
                self.model,
                self.endpoint_label,
            )

    def close(self) -> None:
        self.client.close()
