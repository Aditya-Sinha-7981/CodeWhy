from __future__ import annotations

import math
from pathlib import Path

import pytest

from codewhy.config import Config
from codewhy.extraction.model import FileStructure, UnitInfo
from codewhy.llm.budget import estimate_tokens
from codewhy.llm.chunking import (
    CONTEXT_TOO_SMALL,
    MIN_CHUNK_TOKENS,
    ContextTooSmall,
    plan_chunks,
    source_lines,
    truncation_marker,
)
from codewhy.project import build_project
from codewhy.summarize.file_pass import chunk_overhead, run_file_pass
from codewhy.summarize.prompts import top_level_names
from codewhy.summarize.result import RETRY_RESERVE_TOKENS, pass_budget
from tests.fixtures.builders import (
    OVERSIZED_CLASS,
    OVERSIZED_CLASS_METHODS,
    OVERSIZED_FILE,
    OVERSIZED_FUNCTION,
    OVERSIZED_MEDIUM_FUNCTIONS,
    build_oversized_file,
)
from tests.helpers import FIXED_TIME
from tests.support.fake_provider import FakeProvider

CHUNK_TOKENS = 600
IMPORTED_BY = ["main.py"]
MAX_OUT = 256


def config_for(context_tokens: int) -> Config:
    return Config.model_validate({"llm": {"context_tokens": context_tokens, "max_output_tokens": MAX_OUT}})


def context_for_chunk_budget(fs: FileStructure, chunk_budget: int) -> int:
    """The context_tokens whose pass budget leaves about `chunk_budget` tokens for a chunk's source."""
    overhead = chunk_overhead(fs, top_level_names(fs), [], IMPORTED_BY)
    return math.ceil((chunk_budget + overhead + RETRY_RESERVE_TOKENS) / 0.9) + MAX_OUT + 1


def oversized_case(tmp_path: Path, chunk_tokens: int = CHUNK_TOKENS) -> tuple[FileStructure, Config, int]:
    """The oversized file, a config whose chunk budget is about 1.2 * chunk_tokens, and that chunk budget."""
    project = build_project(build_oversized_file(tmp_path, chunk_tokens=chunk_tokens), Config())
    fs = project.structures[OVERSIZED_FILE]
    assert project.graph.has_edge("main.py", OVERSIZED_FILE)
    config = config_for(context_for_chunk_budget(fs, int(chunk_tokens * 1.2)))
    chunk_budget = pass_budget(config) - chunk_overhead(fs, top_level_names(fs), [], IMPORTED_BY)
    assert int(chunk_tokens * 1.2) <= chunk_budget < int(chunk_tokens * 1.2) + 5
    return fs, config, chunk_budget


@pytest.fixture
def case(tmp_path: Path) -> tuple[FileStructure, Config, int]:
    return oversized_case(tmp_path)


def run(fs: FileStructure, config: Config, fake) -> object:
    return run_file_pass(fs, [], IMPORTED_BY, provider=fake, config=config, clock=lambda: FIXED_TIME)


def unit(fs: FileStructure, name: str) -> UnitInfo:
    return next(u for u in fs.top_level_units if u.name == name)


def boundaries(fs: FileStructure) -> tuple[set[int], set[int]]:
    """Lines where a chunk may start and end without cutting a unit."""
    starts, ends = set(), set()
    for top in fs.top_level_units:
        starts.add(top.start_line)
        ends.add(top.end_line)
        if top.children:
            ends.add(top.children[0].start_line - 1)  # end of the class header
            for child in top.children:
                starts.add(child.start_line)
                ends.add(child.end_line)
    return starts, ends


def chunk_calls(fake: FakeProvider) -> list[str]:
    return [call.prompt for call in fake.calls if "## This part: lines" in call.prompt]


def combine_calls(fake: FakeProvider) -> list[str]:
    return [call.prompt for call in fake.calls if "## Part summaries" in call.prompt]


# --- planner properties -------------------------------------------------------------


def test_fixture_has_the_three_shapes(case) -> None:
    fs, _config, chunk_budget = case
    lines = source_lines(fs.source_text)

    def size(u: UnitInfo) -> int:
        return estimate_tokens("".join(lines[u.start_line - 1 : u.end_line]))

    assert [u.name for u in fs.top_level_units] == [*OVERSIZED_MEDIUM_FUNCTIONS, OVERSIZED_CLASS, OVERSIZED_FUNCTION]
    assert all(size(unit(fs, name)) < chunk_budget / 2 for name in OVERSIZED_MEDIUM_FUNCTIONS[1:])
    assert size(unit(fs, OVERSIZED_CLASS)) > chunk_budget
    assert all(size(child) < chunk_budget for child in unit(fs, OVERSIZED_CLASS).children)
    assert size(unit(fs, OVERSIZED_FUNCTION)) > 2 * chunk_budget


@pytest.mark.parametrize("chunk_budget", range(MIN_CHUNK_TOKENS, 4000, 131))
def test_chunks_cover_the_file_contiguously_on_unit_boundaries_and_fit(case, chunk_budget: int) -> None:
    fs, _config, _ = case
    starts, ends = boundaries(fs)
    chunks = plan_chunks(fs, chunk_budget, 0)
    assert chunks[0].start_line == 1
    assert chunks[-1].end_line == len(source_lines(fs.source_text))
    for before, after in zip(chunks, chunks[1:]):
        assert after.start_line == before.end_line + 1
    for chunk in chunks:
        assert chunk.start_line in starts, f"chunk starts mid-unit at line {chunk.start_line}"
        assert chunk.end_line in ends, f"chunk ends mid-unit at line {chunk.end_line}"
        assert estimate_tokens(chunk.text) <= chunk_budget


def test_untruncated_chunks_are_exact_source_slices(case) -> None:
    fs, _config, chunk_budget = case
    lines = source_lines(fs.source_text)
    chunks = plan_chunks(fs, chunk_budget, 0)
    assert "".join(c.text for c in chunks if not c.omitted_lines) == "".join(
        "".join(lines[c.start_line - 1 : c.end_line]) for c in chunks if not c.omitted_lines
    )
    assert sum(1 for c in chunks if c.omitted_lines) == 1


def test_medium_functions_are_packed_several_per_chunk(case) -> None:
    fs, _config, chunk_budget = case
    chunks = plan_chunks(fs, chunk_budget, 0)
    last_medium = unit(fs, OVERSIZED_MEDIUM_FUNCTIONS[-1]).end_line
    medium_chunks = [c for c in chunks if c.end_line <= last_medium]
    assert 1 < len(medium_chunks) < len(OVERSIZED_MEDIUM_FUNCTIONS)


def test_large_class_is_split_at_methods_with_the_header_on_the_first_chunk(case) -> None:
    fs, _config, chunk_budget = case
    cls = unit(fs, OVERSIZED_CLASS)
    chunks = [c for c in plan_chunks(fs, chunk_budget, 0) if cls.start_line <= c.start_line <= cls.end_line]
    assert len(chunks) > 1
    method_starts = {child.start_line for child in cls.children}
    first = chunks[0]
    assert first.start_line == cls.start_line
    assert f"class {OVERSIZED_CLASS}" in first.text
    assert f"def {OVERSIZED_CLASS_METHODS[0]}" in first.text  # header travels with the first method
    for chunk in chunks[1:]:
        assert chunk.start_line in method_starts
    assert chunks[-1].end_line == cls.end_line


def test_single_oversized_function_is_truncated_with_the_exact_count(case) -> None:
    fs, _config, chunk_budget = case
    big = unit(fs, OVERSIZED_FUNCTION)
    lines = source_lines(fs.source_text)
    (chunk,) = [c for c in plan_chunks(fs, chunk_budget, 0) if c.omitted_lines]
    assert (chunk.start_line, chunk.end_line) == (big.start_line, big.end_line)
    total = big.end_line - big.start_line + 1
    kept = total - chunk.omitted_lines
    assert kept > 0
    assert chunk.text == "".join(lines[big.start_line - 1 : big.start_line - 1 + kept]) + (
        f"[...truncated, {chunk.omitted_lines} lines omitted]\n"
    )
    assert f"def {OVERSIZED_FUNCTION}(" in chunk.text  # the head keeps the signature
    assert "Combine every region" in chunk.text  # and the docstring
    # The head is as long as possible: one more line would not fit.
    longer = "".join(lines[big.start_line - 1 : big.start_line + kept]) + truncation_marker(chunk.omitted_lines - 1)
    assert estimate_tokens(longer) > chunk_budget


def test_too_small_a_chunk_budget_raises(case) -> None:
    fs, _config, _ = case
    with pytest.raises(ContextTooSmall, match="raise llm.context_tokens"):
        plan_chunks(fs, MIN_CHUNK_TOKENS - 1 + 300, 300)
    assert plan_chunks(fs, MIN_CHUNK_TOKENS + 300, 300)


def test_source_lines_follow_newlines_only() -> None:
    assert source_lines("") == []
    assert source_lines("a\nb") == ["a\n", "b"]
    assert source_lines("a\n") == ["a\n"]
    assert source_lines("a\x0cb\n") == ["a\x0cb\n"]  # form feed is not a line break for the parser


def test_gaps_and_overlaps_in_units_never_drop_or_repeat_lines() -> None:
    text = "".join(f"line_{n} = {n}\n" for n in range(1, 41))
    fs = FileStructure(
        path="odd.py",
        language="python",
        source_text=text,
        line_count=40,
        top_level_units=(
            UnitInfo("a", "function", 3, 12),  # lines 1-2 missing
            UnitInfo("b", "function", 10, 25),  # overlaps a
            UnitInfo("c", "function", 27, 30),  # line 26 missing; 31-40 missing
        ),
    )
    chunks = plan_chunks(fs, MIN_CHUNK_TOKENS, 0)
    assert "".join(c.text for c in chunks) == text
    assert chunks[0].start_line == 1 and chunks[-1].end_line == 40


# --- through file_pass ----------------------------------------------------------------


def test_every_chunk_prompt_fits_and_carries_all_top_level_names(case) -> None:
    fs, config, _ = case
    fake = FakeProvider()
    result = run(fs, config, fake)
    assert result.failure is None
    budget = pass_budget(config)
    for call in fake.calls:
        assert estimate_tokens(call.prompt) <= budget
    prompts = chunk_calls(fake)
    assert len(prompts) == len(plan_chunks(fs, budget, chunk_overhead(fs, top_level_names(fs), [], IMPORTED_BY)))
    names = ", ".join(top_level_names(fs))
    for prompt in prompts:
        assert f"## All top-level definitions in this file\n{names}" in prompt
        assert "main.py" in prompt


def test_truncated_function_reaches_the_model_and_the_file_succeeds(case) -> None:
    fs, config, _ = case
    fake = FakeProvider()
    result = run(fs, config, fake)
    assert result.failure is None and result.data is not None
    assert sum("lines omitted]" in prompt for prompt in chunk_calls(fake)) == 1


def test_exactly_one_combine_call_after_the_chunk_calls(case) -> None:
    fs, config, _ = case
    fake = FakeProvider(respond=lambda call: {"purpose": f"Chunk purpose {len(fake.calls)}."})
    result = run(fs, config, fake)
    combines = combine_calls(fake)
    assert len(combines) == 1
    assert fake.calls[-1].prompt == combines[0]
    assert len(chunk_calls(fake)) == len(fake.calls) - 1
    for number in range(1, len(fake.calls)):
        assert f"Part {number}: Chunk purpose {number}." in combines[0]
    assert result.data.purpose == f"Chunk purpose {len(fake.calls)}."


def test_combine_overflow_cuts_chunk_summaries_to_first_sentences(case) -> None:
    fs, config, _ = case
    detail = "It also " + "tracks every metric in detail " * 33 + "."

    def respond(call):
        if "## This part" in call.prompt:
            return {"purpose": f"Short opener {len(fake.calls)}. {detail}"}
        return {"purpose": "Combined."}

    fake = FakeProvider(respond=respond)
    result = run(fs, config, fake)
    assert result.failure is None
    (combine,) = combine_calls(fake)
    assert "Short opener 1." in combine
    assert "tracks every metric" not in combine
    assert estimate_tokens(combine) <= pass_budget(config)


def test_combine_that_cannot_fit_even_cut_fails_with_context_too_small(case) -> None:
    fs, config, _ = case
    fake = FakeProvider(respond=lambda call: {"purpose": "word " * 230})  # no sentence end: cut to 300 chars
    result = run(fs, config, fake)
    assert result.failure == CONTEXT_TOO_SMALL
    assert combine_calls(fake) == []


def test_context_too_small_fails_the_file_without_any_call(case) -> None:
    fs, _config, _ = case
    config = config_for(context_for_chunk_budget(fs, MIN_CHUNK_TOKENS - 50))
    fake = FakeProvider()
    result = run(fs, config, fake)
    assert result.data is None
    assert result.failure == CONTEXT_TOO_SMALL
    assert fake.calls == []
