"""Token estimation and the prompt budget (docs/ARCHITECTURE.md -> Context budgeting).

Pure functions. `estimate_tokens` is a deliberately conservative approximation
for code; never present it to users as an exact count.
"""

from __future__ import annotations

import math

# Fraction of the room left after the output cap that prompts may use.
PROMPT_SHARE = 0.9


def estimate_tokens(text: str) -> int:
    """ceil(len(text) / 3). One function, so a real tokenizer can replace it later."""
    return math.ceil(len(text) / 3)


def prompt_budget(context_tokens: int, max_output_tokens: int) -> int:
    """floor((context_tokens - max_output_tokens) * 0.9): the largest prompt any pass may send."""
    if context_tokens <= 0 or max_output_tokens <= 0:
        raise ValueError("context_tokens and max_output_tokens must be positive")
    if max_output_tokens >= context_tokens:
        raise ValueError(
            f"max_output_tokens ({max_output_tokens}) must be smaller than context_tokens ({context_tokens})"
        )
    return math.floor((context_tokens - max_output_tokens) * PROMPT_SHARE)


def fits(text: str, budget: int) -> bool:
    return estimate_tokens(text) <= budget
