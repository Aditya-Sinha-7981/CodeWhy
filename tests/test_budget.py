from __future__ import annotations

import math
from pathlib import Path

import pytest

from codewhy.config import ConfigError, resolve_config
from codewhy.llm.budget import estimate_tokens, fits, prompt_budget


def test_estimate_tokens_is_ceil_of_a_third() -> None:
    assert [estimate_tokens("x" * n) for n in (0, 1, 2, 3, 4, 300)] == [0, 1, 1, 1, 2, 100]


def test_estimate_tokens_is_deterministic_and_monotonic() -> None:
    text = "def f(x):\n    return x * 2  # ünïcode\n" * 40
    assert estimate_tokens(text) == estimate_tokens(text)
    estimates = [estimate_tokens(text[:n]) for n in range(len(text) + 1)]
    assert estimates == sorted(estimates)


def test_prompt_budget_formula() -> None:
    assert prompt_budget(16384, 1024) == math.floor(15360 * 0.9) == 13824
    assert prompt_budget(32768, 8192) == math.floor(24576 * 0.9)


@pytest.mark.parametrize(("context", "output"), [(1024, 1024), (1000, 2000), (0, 10), (100, 0)])
def test_prompt_budget_rejects_nonsense(context: int, output: int) -> None:
    with pytest.raises(ValueError):
        prompt_budget(context, output)


def test_fits_uses_the_estimate() -> None:
    assert fits("x" * 30, 10)
    assert not fits("x" * 31, 10)


@pytest.mark.parametrize(
    "overrides",
    [
        {"llm.context_tokens": 1024, "llm.max_output_tokens": 1024},
        {"llm.context_tokens": 512},  # ollama's 1024 default output cap no longer fits
        {"llm.provider": "gemini", "llm.context_tokens": 8000},  # gemini's 8192 default does not fit
    ],
)
def test_output_cap_not_below_context_is_a_config_error(tmp_path: Path, overrides: dict[str, object]) -> None:
    with pytest.raises(ConfigError, match=r"max_output_tokens \(\d+\) must be smaller than context_tokens"):
        resolve_config(tmp_path, overrides)
