"""Small, deterministic text helpers shared by the passes (and later the renderer)."""

from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def first_sentence(text: str, max_chars: int = 300) -> str:
    """The first sentence of `text`, whitespace collapsed, cut to `max_chars` with `…`."""
    stripped = text.strip()
    if not stripped:
        return ""
    sentence = " ".join(_SENTENCE_END.split(stripped, maxsplit=1)[0].split())
    if len(sentence) > max_chars:
        sentence = sentence[: max_chars - 1].rstrip() + "…"
    return sentence
