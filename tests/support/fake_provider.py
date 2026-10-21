"""`FakeProvider`: an offline, scriptable `Provider` for every test above the provider layer.

Usage:

    fake = FakeProvider()                              # schema-valid synthesized replies
    fake = FakeProvider(respond=lambda call: {...})    # compute each reply
    fake = FakeProvider(results=[{...}, MalformedOutput("bad"), {...}])  # a queue
    fake = FakeProvider(errors={2: TransientError("down")})              # fail call #2 (0-based)

`fake.calls` records every `generate_json` call in order. `BudgetCheckingFakeProvider`
wraps any provider and fails the test if a prompt exceeds the prompt budget.
"""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from codewhy.llm.budget import estimate_tokens
from codewhy.llm.provider import Provider, ProviderError


@dataclass(frozen=True)
class Call:
    prompt: str
    schema: dict
    max_output_tokens: int
    temperature: float | None


Result = dict | Exception


class FakeProvider:
    def __init__(
        self,
        *,
        respond: Callable[[Call], Result] | None = None,
        results: Iterable[Result] | None = None,
        errors: Mapping[int, Exception] | None = None,
        is_remote: bool = False,
        endpoint_label: str = "fake-provider",
        preflight_error: ProviderError | None = None,
        supports_concurrency: bool = False,
    ) -> None:
        self.respond = respond
        self.results: deque[Result] = deque(results or ())
        self.errors = dict(errors or {})
        self.is_remote = is_remote
        self.endpoint_label = endpoint_label
        self.preflight_error = preflight_error
        self.supports_concurrency = supports_concurrency
        self.calls: list[Call] = []
        self.preflight_calls = 0

    def preflight(self) -> None:
        self.preflight_calls += 1
        if self.preflight_error is not None:
            raise self.preflight_error

    def generate_json(
        self, prompt: str, schema: dict, *, max_output_tokens: int, temperature: float | None = None
    ) -> dict:
        call = Call(prompt, schema, max_output_tokens, temperature)
        index = len(self.calls)
        self.calls.append(call)
        if index in self.errors:
            raise self.errors[index]
        if self.results:
            result = self.results.popleft()
        elif self.respond is not None:
            result = self.respond(call)
        else:
            result = synthesize(schema, prompt)
        if isinstance(result, Exception):
            raise result
        return result


class BudgetCheckingFakeProvider:
    """Delegates to `inner`, asserting `estimate_tokens(prompt) <= prompt_budget` on every call."""

    def __init__(self, inner: Provider, prompt_budget: int) -> None:
        self.inner = inner
        self.prompt_budget = prompt_budget
        self.is_remote = inner.is_remote
        self.endpoint_label = inner.endpoint_label

    @property
    def calls(self) -> list[Call]:
        return getattr(self.inner, "calls", [])

    def preflight(self) -> None:
        self.inner.preflight()

    def generate_json(
        self, prompt: str, schema: dict, *, max_output_tokens: int, temperature: float | None = None
    ) -> dict:
        estimate = estimate_tokens(prompt)
        assert estimate <= self.prompt_budget, (
            f"prompt of ~{estimate} tokens exceeds the prompt budget of {self.prompt_budget}"
        )
        return self.inner.generate_json(prompt, schema, max_output_tokens=max_output_tokens, temperature=temperature)


def synthesize(schema: Mapping[str, Any], prompt: str) -> dict:
    """A deterministic dict satisfying `schema`'s required fields (text derived from a hash of the prompt)."""
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    value = _value(schema, digest, "value")
    return value if isinstance(value, dict) else {}


def _value(schema: Mapping[str, Any], digest: str, name: str) -> Any:
    kind = schema.get("type")
    if kind == "object":
        properties = schema.get("properties", {})
        return {key: _value(properties.get(key, {}), digest, key) for key in schema.get("required", [])}
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
    return f"Fake {name} {digest}."
