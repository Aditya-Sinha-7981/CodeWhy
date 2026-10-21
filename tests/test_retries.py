from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from codewhy.config import Config
from codewhy.llm.budget import estimate_tokens
from codewhy.llm.provider import AuthError, BadRequestError, MalformedOutput, TransientError
from codewhy.pipeline import run_pipeline
from codewhy.project import build_project
from codewhy.summarize.prompts import retry_suffix
from codewhy.summarize.result import (
    RETRY_ERROR_CHARS,
    RETRY_RESERVE_TOKENS,
    RETRY_TEMPERATURE,
    PassFailed,
    PassResult,
    call_pass,
    describe_error,
)
from codewhy.summarize.schemas import FilePass
from tests.helpers import FIXED_TIME
from tests.support.fake_provider import FakeProvider

FIXTURES = Path(__file__).parent / "fixtures"
PROMPT = "Explain this file."
BUDGET = 10_000


def call(fake: FakeProvider) -> FilePass:
    return call_pass(fake, PROMPT, FilePass, budget=BUDGET, max_output_tokens=512)


@pytest.mark.parametrize(
    ("bad", "error_text"),
    [
        (MalformedOutput("the reply did not contain a JSON object", raw="Sure! Here you go"), "did not contain a JSON object"),
        ({"summary": "wrong field"}, "purpose: Field required"),
        ({"purpose": "   "}, "purpose: String should have at least 1 character"),
    ],
    ids=["unparseable", "schema-invalid", "empty-required"],
)
def test_malformed_output_is_retried_once_with_the_error(bad, error_text: str) -> None:
    fake = FakeProvider(results=[bad, {"purpose": "Fixed."}])
    assert call(fake) == FilePass(purpose="Fixed.")
    assert len(fake.calls) == 2
    first, retry = fake.calls
    assert (first.prompt, first.temperature) == (PROMPT, None)
    assert retry.prompt.startswith(PROMPT + "\n\nYour previous response was invalid: ")
    assert error_text in retry.prompt
    assert retry.prompt.endswith("Reply with only a JSON object matching the schema.")
    assert retry.temperature == RETRY_TEMPERATURE == 0.2
    assert retry.max_output_tokens == first.max_output_tokens == 512


def test_second_failure_raises_pass_failed_after_exactly_two_calls() -> None:
    fake = FakeProvider(results=[{"purpose": ""}, {"purpose": ""}, {"purpose": "never reached"}])
    with pytest.raises(PassFailed, match="invalid output after one retry: purpose"):
        call(fake)
    assert len(fake.calls) == 2


def test_transient_error_fails_without_a_retry() -> None:
    fake = FakeProvider(results=[TransientError("rate limited 4 times"), {"purpose": "never reached"}])
    with pytest.raises(PassFailed, match="provider unavailable: rate limited"):
        call(fake)
    assert len(fake.calls) == 1


def test_transient_error_on_the_retry_also_fails() -> None:
    fake = FakeProvider(results=[MalformedOutput("bad"), TransientError("down")])
    with pytest.raises(PassFailed, match="provider unavailable"):
        call(fake)
    assert len(fake.calls) == 2


@pytest.mark.parametrize("error", [AuthError("bad key"), BadRequestError("unknown model")])
def test_configuration_errors_propagate(error) -> None:
    with pytest.raises(type(error)):
        call(FakeProvider(results=[error]))


def test_failure_reasons_never_contain_the_models_text() -> None:
    secret = "MODEL-TEXT-" + "z" * 1300  # too long for the schema
    fake = FakeProvider(results=[{"purpose": secret}, {"purpose": secret}])
    with pytest.raises(PassFailed) as info:
        call(fake)
    assert "MODEL-TEXT" not in info.value.reason
    assert "MODEL-TEXT" not in fake.calls[1].prompt[len(PROMPT) :]


def test_error_text_in_the_retry_is_capped_and_fits_the_reserve() -> None:
    error = MalformedOutput("x" * 5000)
    text = describe_error(error)
    assert len(text) == RETRY_ERROR_CHARS and text.endswith("…")
    assert estimate_tokens(retry_suffix(text)) <= RETRY_RESERVE_TOKENS


def test_describe_validation_error_is_one_line() -> None:
    with pytest.raises(ValidationError) as info:
        FilePass.model_validate({"purpose": "", "extra": 1})
    text = describe_error(info.value)
    assert "\n" not in text
    assert "purpose:" in text and "extra:" in text


def test_a_prompt_over_budget_is_never_sent() -> None:
    fake = FakeProvider()
    with pytest.raises(PassFailed, match="exceeded the context budget"):
        call_pass(fake, "x" * 31, FilePass, budget=10, max_output_tokens=10)
    assert fake.calls == []


def test_pass_result_requires_exactly_one_of_data_and_failure() -> None:
    with pytest.raises(ValueError):
        PassResult(data=None, metadata={}, generated_at=FIXED_TIME, failure=None)
    with pytest.raises(ValueError):
        PassResult(data=FilePass(purpose="p"), metadata={}, generated_at=FIXED_TIME, failure="boom")


# --- through the pipeline -------------------------------------------------------------------


def test_a_twice_malformed_file_fails_and_the_run_continues() -> None:
    project = build_project(FIXTURES / "flat_scripts", Config())

    def respond(call):
        if "path: solver.py\n" in call.prompt and "## Full source" in call.prompt:
            return {"purpose": ""}
        return {name: "Text." for name in call.schema["required"]}

    fake = FakeProvider(respond=respond)
    result = run_pipeline(project, fake, Config(), clock=lambda: FIXED_TIME, use_cache=False)
    assert result.files["solver.py"].failure is not None
    assert result.files["solver.py"].failure.startswith("invalid output after one retry")
    assert result.files["main.py"].failure is None
    assert result.modules["."].failure is None
    assert result.project.failure is None
    assert result.counts == {"generated": 4, "cached": 0, "failed": 1, "skipped": 0}
    assert len(fake.calls) == 3 + 1 + 1 + 1  # 3 files + 1 retry + module + project


@pytest.mark.parametrize("error", [AuthError("bad key"), BadRequestError("unknown model")])
def test_configuration_errors_abort_the_whole_run(error) -> None:
    project = build_project(FIXTURES / "flat_scripts", Config())
    fake = FakeProvider(errors={1: error})
    with pytest.raises(type(error)):
        run_pipeline(project, fake, Config(), clock=lambda: FIXED_TIME, use_cache=False)
    assert len(fake.calls) == 2
