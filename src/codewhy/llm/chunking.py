"""AST-boundary chunking for files whose prompt exceeds the budget.

docs/ARCHITECTURE.md -> Chunking (locked). Pure: the planner only slices
`source_text` along `top_level_units`; it never calls a provider.

1. Consecutive units are packed greedily into chunks whose source fits the
   chunk budget (prompt budget minus the chunk prompt's fixed overhead).
2. A class too large for one chunk is split at method boundaries; its header
   (the lines before the first method's range) travels with the first method.
3. A single function/method (or any other unsplittable piece) too large for a
   chunk keeps its head and ends with `[...truncated, N lines omitted]`.

Every source line belongs to exactly one chunk; chunks are contiguous and in order.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from codewhy.extraction.model import FileStructure, UnitInfo
from codewhy.llm.budget import estimate_tokens

# Below this many estimated tokens of room for source, a chunk is too small to be useful.
MIN_CHUNK_TOKENS = 500
CONTEXT_TOO_SMALL = "context too small for this file; raise llm.context_tokens"


class ContextTooSmall(Exception):
    """The budget leaves no useful room for source; the file fails with an actionable reason."""

    def __init__(self) -> None:
        super().__init__(CONTEXT_TOO_SMALL)


@dataclass(frozen=True)
class Chunk:
    """Lines `start_line..end_line` of a file (1-based, inclusive) and the text sent for them."""

    start_line: int
    end_line: int
    text: str  # ends with the truncation marker when omitted_lines > 0
    omitted_lines: int = 0


@dataclass(frozen=True)
class _Piece:
    start: int
    end: int
    unit: UnitInfo | None  # None for a class header or a gap filler


def truncation_marker(omitted: int) -> str:
    return f"[...truncated, {omitted} lines omitted]\n"


def source_lines(text: str) -> list[str]:
    """`text` split on `\\n` (matching parser line numbers), each line keeping its newline."""
    if not text:
        return []
    lines = [line + "\n" for line in text.split("\n")]
    if text.endswith("\n"):
        lines.pop()
    else:
        lines[-1] = lines[-1][:-1]
    return lines


def plan_chunks(fs: FileStructure, budget: int, overhead_tokens: int) -> list[Chunk]:
    """Chunks of `fs` whose source text each fits `budget - overhead_tokens` estimated tokens."""
    chunk_budget = budget - overhead_tokens
    if chunk_budget < MIN_CHUNK_TOKENS:
        raise ContextTooSmall()
    lines = source_lines(fs.source_text)
    if not lines:
        return []

    def size(start: int, end: int) -> int:
        return estimate_tokens("".join(lines[start - 1 : end]))

    pieces: list[_Piece] = []
    for piece in _tile(fs.top_level_units, 1, len(lines)):
        if size(piece.start, piece.end) > chunk_budget and piece.unit is not None and piece.unit.children:
            pieces.extend(_split_class(piece, chunk_budget, size))
        else:
            pieces.append(piece)

    chunks: list[Chunk] = []
    current: tuple[int, int] | None = None
    for piece in pieces:
        if size(piece.start, piece.end) > chunk_budget:
            if current:
                chunks.append(_plain(lines, *current))
                current = None
            chunks.append(_truncated(lines, piece.start, piece.end, chunk_budget))
        elif current and size(current[0], piece.end) <= chunk_budget:
            current = (current[0], piece.end)
        else:
            if current:
                chunks.append(_plain(lines, *current))
            current = (piece.start, piece.end)
    if current:
        chunks.append(_plain(lines, *current))
    return chunks


def _tile(units: tuple[UnitInfo, ...], first: int, last: int) -> list[_Piece]:
    """Pieces covering lines first..last exactly once, following the units' ranges.

    Extraction already tiles the file; this also absorbs any gap or overlap so a
    quirk upstream can never drop or duplicate a line.
    """
    pieces: list[_Piece] = []
    covered = first - 1
    for unit in sorted(units, key=lambda u: (u.start_line, u.end_line)):
        end = min(unit.end_line, last)
        if end <= covered:
            continue
        pieces.append(_Piece(covered + 1, end, unit))  # a leading gap joins the following unit
        covered = end
    if covered < last:
        if pieces:
            tail = pieces[-1]
            pieces[-1] = _Piece(tail.start, last, tail.unit)
        else:
            pieces.append(_Piece(first, last, None))
    return pieces


def _split_class(piece: _Piece, chunk_budget: int, size: Callable[[int, int], int]) -> list[_Piece]:
    assert piece.unit is not None
    first_method = min(child.start_line for child in piece.unit.children)
    methods = _tile(piece.unit.children, max(first_method, piece.start), piece.end)
    header_end = methods[0].start - 1
    if header_end < piece.start:
        return methods
    header = _Piece(piece.start, header_end, None)
    # The header attaches to the first method's chunk whenever the two fit together.
    if size(header.start, methods[0].end) <= chunk_budget:
        return [_Piece(header.start, methods[0].end, methods[0].unit), *methods[1:]]
    return [header, *methods]


def _plain(lines: list[str], start: int, end: int) -> Chunk:
    return Chunk(start, end, "".join(lines[start - 1 : end]))


def _truncated(lines: list[str], start: int, end: int, chunk_budget: int) -> Chunk:
    """Keep as many leading lines as fit, then the marker with the exact omitted count."""
    body = lines[start - 1 : end]
    total = len(body)

    def text(kept: int) -> str:
        return "".join(body[:kept]) + truncation_marker(total - kept)

    # The largest head that fits; at least one line is always omitted. Each kept
    # line adds at least its newline while the marker shrinks by at most one
    # digit, so the size only grows with `kept` and a binary search is exact.
    # Kept lines end with their newline, so the marker starts on its own line.
    low, high = 0, total - 1
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_tokens(text(middle)) <= chunk_budget:
            low = middle
        else:
            high = middle - 1
    return Chunk(start, end, text(low), omitted_lines=total - low)
