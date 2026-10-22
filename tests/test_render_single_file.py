"""Single-file rendering: golden layout per level, level selection, timestamps, safety.

Golden files freeze the exact layout. To update them after an intended layout
change, review the diff by eye and log it in the task's work log:

    CODEWHY_UPDATE_GOLDEN=1 .venv/bin/python -m pytest -q tests/test_render_single_file.py tests/test_render_docs_tree.py
"""

from __future__ import annotations

import os
import re
from datetime import timedelta
from pathlib import Path

import pytest

from codewhy.config import Config, OutputConfig
from codewhy.llm.budget import estimate_tokens
from codewhy.pipeline import run_pipeline
from codewhy.project import build_project
from codewhy.render.sections import PREAMBLE
from codewhy.render.single_file import _render, render_single_file
from tests.helpers import FIXED_TIME
from tests.support.fake_provider import Call, FakeProvider

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"
ARCHITECTURE = Path(__file__).resolve().parents[1] / "docs" / "ARCHITECTURE.md"
UPDATE_GOLDEN = os.environ.get("CODEWHY_UPDATE_GOLDEN") == "1"
VERSION = "test"

# Scripted answers, keyed by the subject each prompt is about.
FILE_ANSWERS = {
    "main.py": {"purpose": "Runs the example: it asks the solver for a placement on a small board and prints it."},
    "solver.py": {
        "purpose": "Holds the backtracking search that main.py calls to place the queens.",
        "notes": "solve imports is_safe lazily, so it returns an empty board if utils lacks it.",
    },
    "utils.py": {"purpose": "Provides the board helpers the solver and main.py share. It keeps the search code small."},
}
MODULE_ANSWER = {
    "responsibility": "Contains the whole N-Queens example. Everything lives in the project root.",
    "how_pieces_cooperate": "main.py drives solver.py, and both rely on the helpers in utils.py.",
}
PROJECT_ANSWER = {
    "overview": "A small command-line example that solves the N-Queens puzzle by backtracking.",
    "architecture": "main.py calls solve in solver.py, which places queens using the checks in utils.py.",
}


def scripted(call: Call) -> dict:
    if "## Project\n" in call.prompt:
        return PROJECT_ANSWER
    if "## Module\n" in call.prompt:
        return MODULE_ANSWER
    path = call.prompt.split("## File\npath: ", 1)[1].split("\n", 1)[0]
    return FILE_ANSWERS[path]


def scripted_run(config: Config | None = None, respond=scripted, clock=lambda: FIXED_TIME):
    config = config or Config()
    project = build_project(FIXTURES / "flat_scripts", config)
    return project, run_pipeline(project, FakeProvider(respond=respond), config, clock=clock, use_cache=False), config


def check_golden(name: str, text: str) -> None:
    path = GOLDEN / name
    if UPDATE_GOLDEN:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    assert text == path.read_text(encoding="utf-8"), f"{name} differs from the golden file"


def headings(markdown: str) -> list[str]:
    """Headings a CommonMark renderer would produce: ATX (≤3 spaces) and setext, outside fences."""
    found: list[str] = []
    fence: str | None = None
    previous = ""
    if markdown.startswith("---\n"):  # frontmatter is metadata, not body
        markdown = markdown[4:].split("\n---\n", 1)[1]
    for line in markdown.split("\n"):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence is not None:
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence):
                fence = None
            previous = ""
            continue
        if marker:
            fence = marker.group(1)
        elif re.match(r"^ {0,3}#{1,6}(\s|$)", line):
            found.append(line.strip())
        elif re.match(r"^ {0,3}(=+|-+)\s*$", line) and previous.strip():
            found.append(f"setext: {previous.strip()}")
        previous = line
    assert fence is None, "the document ends inside an unclosed code fence"
    return found


@pytest.mark.parametrize("level", [0, 1, 2])
def test_single_file_levels_match_goldens(level: int) -> None:
    project, run, _ = scripted_run()
    check_golden(f"flat_scripts_level{level}.md", _render(project, run, version=VERSION, level=level))


def test_level_0_layout_header_order_and_newline() -> None:
    project, run, config = scripted_run()
    doc = render_single_file(project, run, config, version=VERSION)
    assert doc.level == 0 and not doc.over_budget
    text = doc.text
    assert text.startswith("---\ngenerated_by: codewhy vtest\nsource: project\ngenerated_at: 2026-01-01T12:00:00Z\n---\n\n")
    assert text.count("generated_by:") == 1 and text.count("\n---\n") == 1
    order = [text.index(h) for h in ("# flat_scripts — codebase documentation", PREAMBLE, "## Overview", "## Modules", "## Files")]
    assert order == sorted(order)
    files = [line for line in text.split("\n") if line.startswith("### `") and line.endswith(".py`")]
    assert files == ["### `main.py`", "### `solver.py`", "### `utils.py`"]
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert "\r" not in text


def test_preamble_is_exactly_the_architecture_paragraph() -> None:
    doc = ARCHITECTURE.read_text(encoding="utf-8")
    block = doc.split("Exact text (frozen by a golden-file test):\n\n", 1)[1].split("\n\n", 1)[0]
    assert PREAMBLE == "\n".join(line.removeprefix("> ") for line in block.split("\n"))


def test_notes_only_at_level_0_and_defined_here_dropped_at_level_1() -> None:
    project, run, _ = scripted_run()
    level0, level1 = (_render(project, run, version=VERSION, level=n) for n in (0, 1))
    assert level0.count("#### Notes") == 1 and "#### Defined Here" in level0
    assert "#### Notes" not in level1 and "#### Defined Here" not in level1
    assert "#### Depends On" in level1 and "#### Used By" in level1


def test_level_2_is_minimal() -> None:
    project, run, _ = scripted_run()
    text = _render(project, run, version=VERSION, level=2)
    assert "- `solver.py` — Holds the backtracking search that main.py calls to place the queens." in text
    assert "### `(project root)`\n\nContains the whole N-Queens example." in text
    for dropped in ("#### Responsibility", "#### Purpose", "### Modules", "### Most depended-on files"):
        assert dropped not in text
    for kept in ("## Overview", "### Architecture", "### Entry Point"):
        assert kept in text


def test_generated_at_is_the_latest_pass_result_not_the_wall_clock() -> None:
    ticks = iter(FIXED_TIME + timedelta(minutes=n) for n in range(100))
    project, run, config = scripted_run(clock=lambda: next(ticks))
    latest = max(r.generated_at for r in [*run.files.values(), *run.modules.values(), run.project])
    assert latest == FIXED_TIME + timedelta(minutes=4)
    doc = render_single_file(project, run, config, version=VERSION)
    assert "generated_at: 2026-01-01T12:04:00Z\n" in doc.text


def test_levels_are_chosen_by_estimated_size() -> None:
    project, run, _ = scripted_run()
    sizes = [estimate_tokens(_render(project, run, version=VERSION, level=n)) for n in (0, 1, 2)]
    assert sizes[0] > sizes[1] > sizes[2]
    for budget, level, over in (
        (sizes[0], 0, False),
        (sizes[0] - 1, 1, False),
        (sizes[1], 1, False),
        (sizes[1] - 1, 2, False),
        (sizes[2], 2, False),
        (sizes[2] - 1, 2, True),
    ):
        doc = render_single_file(project, run, Config(output=OutputConfig(token_budget=budget)), version=VERSION)
        assert (doc.level, doc.over_budget) == (level, over), budget
        assert doc.estimated_tokens == estimate_tokens(doc.text) == sizes[level]


INJECTION = (
    "Starts plainly.\n"
    "# Fake heading\n"
    "   ## Indented heading\n"
    "Setext title\n"
    "---\n"
    "Another\n"
    "===\n"
    "```\n"
    "an unclosed fence\n"
    "<!-- an unclosed comment\n"
    "~~~"
)


def test_model_text_can_never_change_document_structure() -> None:
    def benign(call: Call) -> dict:
        return {name: "Plain text." for name in call.schema["required"]} | (
            {"notes": "Plain text."} if "## File\n" in call.prompt else {}
        )

    def hostile(call: Call) -> dict:
        return {name: INJECTION for name in call.schema["required"]} | (
            {"notes": INJECTION} if "## File\n" in call.prompt else {}
        )

    project, safe_run, _ = scripted_run(respond=benign)
    _, evil_run, _ = scripted_run(respond=hostile)
    for level in (0, 1, 2):
        expected = headings(_render(project, safe_run, version=VERSION, level=level))
        actual = headings(_render(project, evil_run, version=VERSION, level=level))
        assert actual == expected
    level0 = _render(project, evil_run, version=VERSION, level=0)
    assert "\\# Fake heading" in level0 and "\\```" in level0 and "\\<!--" in level0
