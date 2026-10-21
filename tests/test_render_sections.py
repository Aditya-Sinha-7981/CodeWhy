from __future__ import annotations

from pathlib import Path

from codewhy.config import Config
from codewhy.llm.provider import TransientError
from codewhy.pipeline import run_pipeline
from codewhy.project import build_project
from codewhy.render.sections import escape_llm_text, file_section, project_overview_sections
from tests.helpers import FIXED_TIME
from tests.support.fake_provider import FakeProvider

FIXTURES = Path(__file__).parent / "fixtures"


def test_escape_llm_text_normalizes_and_escapes_headings() -> None:
    assert escape_llm_text("# Heading\r\ntext  \r\n```\r\n") == "\\# Heading\ntext\n```"


def test_file_section_contains_deterministic_dependencies_and_definitions() -> None:
    project = build_project(FIXTURES / "flat_scripts", Config())
    result = run_pipeline(project, FakeProvider(), Config(), clock=lambda: FIXED_TIME)
    text = file_section(project, result, "main.py")
    assert "#### Depends On" in text
    assert "`solver.py`" in text
    assert "#### Defined Here" in text
    assert "`def main" in text


def test_failed_result_is_visible_and_empty_sections_use_none() -> None:
    project = build_project(FIXTURES / "flat_scripts", Config())
    result = run_pipeline(project, FakeProvider(errors={0: TransientError("fail")}), Config(), clock=lambda: FIXED_TIME)
    text = file_section(project, result, "main.py")
    assert "[generation failed]" in text
    assert "#### Notes" not in text
    overview = project_overview_sections(project, result)
    assert "### Most depended-on files" in overview
