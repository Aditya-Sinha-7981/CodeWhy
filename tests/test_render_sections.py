from __future__ import annotations

from pathlib import Path

import pytest

from codewhy.config import Config
from codewhy.llm.provider import TransientError
from codewhy.pipeline import run_pipeline
from codewhy.project import build_project
from codewhy.render.sections import (
    FAILED,
    escape_llm_text,
    file_section,
    module_section,
    project_overview_sections,
)
from tests.helpers import FIXED_TIME, make_project
from tests.support.fake_provider import Call, FakeProvider

FIXTURES = Path(__file__).parent / "fixtures"


def run(root: Path, fake: FakeProvider | None = None):
    project = build_project(root, Config())
    return project, run_pipeline(project, fake or FakeProvider(), Config(), clock=lambda: FIXED_TIME)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# Heading\r\ntext  \r\n", "\\# Heading\ntext"),
        ("Intro\n   ## Indented", "Intro\n   \\## Indented"),
        ("    # leading indent is stripped first", "\\# leading indent is stripped first"),
        ("Title\n---", "Title\n\\---"),
        ("Title\n===", "Title\n\\==="),
        ("```\ncode", "\\```\ncode"),
        ("~~~python", "\\~~~python"),
        ("<!-- hidden", "\\<!-- hidden"),
        ("***", "\\***"),
        ("Issue #12 is open.\n#12 again", "Issue #12 is open.\n\\#12 again"),
        ("Uses a - b and a * b inline.", "Uses a - b and a * b inline."),
        ("line\r\rnext", "line\n\nnext"),
    ],
)
def test_escape_llm_text(text: str, expected: str) -> None:
    assert escape_llm_text(text) == expected


def test_defined_here_lists_classes_methods_functions_and_types_in_source_order(tmp_path: Path) -> None:
    make_project(
        tmp_path,
        {
            "shapes.py": (
                "def area(shape) -> float:\n    return 0.0\n\n\n"
                "class Square(Shape):\n    def side(self) -> int:\n        return 1\n\n    def grow(self, by: int) -> None:\n        pass\n\n\n"
                "def perimeter(shape):\n    return 4\n"
            ),
            "types.ts": "export interface Point { x: number }\nexport function origin(): Point { return { x: 0 } }\nexport type Id = string\n",
        },
    )
    project, result = run(tmp_path)
    py = file_section(project, result, "shapes.py").split("#### Defined Here\n\n", 1)[1]
    assert py.split("\n")[:5] == [
        "- `def area(shape) -> float`",
        "- `class Square(Shape)`",
        "  - `def side(self) -> int`",
        "  - `def grow(self, by: int) -> None`",
        "- `def perimeter(shape)`",
    ]
    ts = file_section(project, result, "types.ts").split("#### Defined Here\n\n", 1)[1]
    assert ts.split("\n")[:3] == ["- `interface Point`", "- `export function origin(): Point`", "- `type Id`"]


def test_empty_lists_render_none_and_definitions_none(tmp_path: Path) -> None:
    make_project(tmp_path, {"alone.py": "VALUE = 1\n"})
    project, result = run(tmp_path)
    text = file_section(project, result, "alone.py")
    assert "#### Depends On\n\n- None" in text
    assert "#### Used By\n\n- None" in text
    assert "#### Defined Here\n\n- None" in text
    module = module_section(project, result, project.modules[0])
    assert "#### Internal dependencies\n\n- None" in module
    assert "#### External dependencies\n\n- None" in module


def test_notes_rendered_only_when_present() -> None:
    def respond(call: Call) -> dict:
        if "path: solver.py\n" in call.prompt:
            return {"purpose": "Solves.", "notes": "A caveat."}
        if "## File\n" in call.prompt:
            return {"purpose": "Helps.", "notes": "   "}
        return {name: "Text." for name in call.schema["required"]}

    project, result = run(FIXTURES / "flat_scripts", FakeProvider(respond=respond))
    assert "#### Notes\n\nA caveat." in file_section(project, result, "solver.py")
    assert "#### Notes" not in file_section(project, result, "utils.py")


def test_most_depended_on_omitted_when_no_edges(tmp_path: Path) -> None:
    make_project(tmp_path, {"a.py": "x = 1\n", "b.py": "y = 2\n"})
    project, result = run(tmp_path)
    assert "Most depended-on files" not in project_overview_sections(project, result)


def test_other_files_capped_at_100(tmp_path: Path) -> None:
    files = {"main.py": "x = 1\n"} | {f"assets/img_{n:03}.png": "" for n in range(105)}
    project, result = run(make_project(tmp_path, files))
    overview = project_overview_sections(project, result)
    listed = [line for line in overview.split("\n") if line.startswith("- `assets/")]
    assert len(listed) == 100 and listed[-1] == "- `assets/img_099.png`"
    assert overview.endswith("- … and 5 more")


def test_root_module_is_named_project_root() -> None:
    project, result = run(FIXTURES / "flat_scripts")
    assert module_section(project, result, project.modules[0]).startswith("### `(project root)`")
    assert "- `(project root)` — " in project_overview_sections(project, result)


def test_entry_point_none_is_stated(tmp_path: Path) -> None:
    project, result = run(make_project(tmp_path, {"lib.py": "x = 1\n"}))
    assert "### Entry Point\n\nNo conventional entry point detected." in project_overview_sections(project, result)


def test_failed_results_show_the_placeholder_under_their_own_headings() -> None:
    # Calls: main.py, solver.py, utils.py, module ".", project.
    fake = FakeProvider(errors={0: TransientError("down"), 3: TransientError("down"), 4: TransientError("down")})
    project, result = run(FIXTURES / "flat_scripts", fake)
    file_text = file_section(project, result, "main.py")
    assert f"#### Purpose\n\n{FAILED}\n" in file_text
    assert "#### Notes" not in file_text
    assert FAILED not in file_section(project, result, "solver.py")
    module = module_section(project, result, project.modules[0])
    assert f"#### Responsibility\n\n{FAILED}\n" in module
    assert f"#### How the pieces cooperate\n\n{FAILED}\n" in module
    overview = project_overview_sections(project, result)
    assert f"## Overview\n\n{FAILED}\n" in overview
    assert f"### Architecture\n\n{FAILED}\n" in overview
    assert f"- `(project root)` — {FAILED}" in overview
