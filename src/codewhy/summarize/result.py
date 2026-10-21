"""`PassResult` (the Layer 5 seam) and the call-with-retry helper every pass uses.

docs/ARCHITECTURE.md -> Retries and failure, Layer 5. Malformed output gets
exactly one retry with the error appended; a second failure, or a transient
provider error that survived the provider's own backoff, becomes a failed
result. `AuthError` and `BadRequestError` are configuration problems and
propagate to abort the whole run.

Logging rule: nothing here logs prompts or model output; failure reasons carry
error descriptions only, never the model's text.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from codewhy.config import Config
from codewhy.llm.budget import estimate_tokens, fits, prompt_budget
from codewhy.llm.provider import MalformedOutput, Provider, TransientError
from codewhy.summarize.prompts import retry_suffix
from codewhy.summarize.schemas import provider_schema

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]
RequestCallback = Callable[[str, str], None]
Model = TypeVar("Model", bound=BaseModel)

RETRY_TEMPERATURE = 0.2
RETRY_ERROR_CHARS = 500
# Room kept free in every prompt for the retry suffix, so a retry can never
# push a prompt over the budget (the suffix carries at most RETRY_ERROR_CHARS).
RETRY_RESERVE_TOKENS = estimate_tokens(retry_suffix("x" * RETRY_ERROR_CHARS))


@dataclass(frozen=True)
class PassResult:
    data: BaseModel | None
    metadata: dict[str, Any]  # always {} in v1 (Layer 5 reserved)
    generated_at: datetime
    failure: str | None

    def __post_init__(self) -> None:
        if (self.data is None) == (self.failure is None):
            raise ValueError("exactly one of data and failure must be set")

    @classmethod
    def ok(cls, data: BaseModel, now: datetime) -> PassResult:
        return cls(data=data, metadata={}, generated_at=now, failure=None)

    @classmethod
    def failed(cls, reason: str, now: datetime) -> PassResult:
        return cls(data=None, metadata={}, generated_at=now, failure=reason)


class PassFailed(Exception):
    """A pass gave up on one item; the reason becomes `PassResult.failure`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def pass_budget(config: Config) -> int:
    """The budget a pass assembles prompts against: the prompt budget minus the retry reserve."""
    llm = config.llm
    return prompt_budget(llm.effective_context_tokens, llm.effective_max_output_tokens) - RETRY_RESERVE_TOKENS


def describe_error(error: Exception) -> str:
    """A short, single-line description of why output was rejected, without the model's text."""
    if isinstance(error, ValidationError):
        parts = []
        for item in error.errors(include_url=False, include_input=False):
            where = ".".join(str(part) for part in item["loc"]) or "response"
            parts.append(f"{where}: {item['msg']}")
        text = "; ".join(parts)
    else:
        text = str(error)
    text = " ".join(text.split())
    return text if len(text) <= RETRY_ERROR_CHARS else text[: RETRY_ERROR_CHARS - 1] + "…"


def call_pass(
    provider: Provider,
    prompt: str,
    model_cls: type[Model],
    *,
    budget: int,
    max_output_tokens: int,
    temperature: float | None = None,
    on_request: RequestCallback | None = None,
) -> Model:
    """One validated result from `provider`, retrying malformed output once. Raises PassFailed.

    `budget` is the pass budget the prompt was assembled against (`pass_budget`).
    Checking it here is a last guard: a prompt that does not fit is never sent.
    """
    if not fits(prompt, budget):
        raise PassFailed("internal error: an assembled prompt exceeded the context budget and was not sent")
    schema = provider_schema(model_cls)
    attempt_prompt, attempt_temperature = prompt, temperature
    for attempt in (1, 2):
        if on_request is not None:
            on_request(
                "started",
                f"attempt {attempt}/2; prompt ~{estimate_tokens(attempt_prompt)} tokens; output cap {max_output_tokens}",
            )
        completed = False
        try:
            raw = provider.generate_json(
                attempt_prompt, schema, max_output_tokens=max_output_tokens, temperature=attempt_temperature
            )
            result = model_cls.model_validate(raw)
            completed = True
            return result
        except TransientError as exc:
            raise PassFailed(f"provider unavailable: {exc}") from None
        except (MalformedOutput, ValidationError) as exc:
            error = describe_error(exc)
            if attempt == 2:
                raise PassFailed(f"invalid output after one retry: {error}") from None
            logger.debug("malformed %s output; retrying once", model_cls.__name__)
            attempt_prompt = prompt + retry_suffix(error)
            attempt_temperature = RETRY_TEMPERATURE
        finally:
            if on_request is not None:
                on_request("complete" if completed else "failed", f"attempt {attempt}/2")
    raise AssertionError("unreachable")
