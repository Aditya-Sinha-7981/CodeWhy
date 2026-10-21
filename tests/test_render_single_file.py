from __future__ import annotations

from pathlib import Path

from codewhy.config import Config, OutputConfig
from codewhy.pipeline import run_pipeline
from codewhy.project import build_project
from codewhy.render.single_file import render_single_file
from tests.helpers import FIXED_TIME
from tests.support.fake_provider import FakeProvider

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"


def rendered(budget: int = 20000):
    config = Config(output=OutputConfig(token_budget=budget))
    project = build_project(FIXTURES / "flat_scripts", config)
    result = run_pipeline(project, FakeProvider(), config, clock=lambda: FIXED_TIME)
    return render_single_file(project, result, config, version="test"), project, result


def test_level_zero_layout_timestamp_and_newline() -> None:
    doc, _, _ = rendered()
    assert doc.level == 0
    assert doc.text.startswith("---\ngenerated_by: codewhy vtest\nsource: project\ngenerated_at: 2026-01-01T12:00:00Z\n---\n")
    assert "# flat_scripts — codebase documentation" in doc.text
    assert doc.text.count("generated_by:") == 1
    assert doc.text.endswith("\n") and not doc.text.endswith("\n\n")


def test_single_file_levels_match_goldens() -> None:
    for name, budget in (("flat_scripts_level0.md", 20000), ("flat_scripts_level1.md", 638), ("flat_scripts_level2.md", 1)):
        config = Config(output=OutputConfig(token_budget=budget))
        project = build_project(FIXTURES / "flat_scripts", config)
        result = run_pipeline(project, FakeProvider(), config, clock=lambda: FIXED_TIME)
        doc = render_single_file(project, result, config, version="test")
        assert doc.text == (GOLDEN / name).read_text(encoding="utf-8")


def test_levels_are_selected_by_estimated_size() -> None:
    compact, _, _ = rendered(500)
    assert compact.level in {1, 2}
    tiny, _, _ = rendered(1)
    assert tiny.level == 2
    assert tiny.over_budget


def test_model_headings_are_escaped() -> None:
    config = Config()
    project = build_project(FIXTURES / "flat_scripts", config)
    fake = FakeProvider(respond=lambda call: {"purpose": "# Heading\nplain", "notes": "```\ncode"} if "path:" in call.prompt else {"responsibility": "r", "how_pieces_cooperate": "h", "overview": "o", "architecture": "a"})
    result = run_pipeline(project, fake, config, clock=lambda: FIXED_TIME)
    doc = render_single_file(project, result, config, version="test")
    assert "\\# Heading" in doc.text
    assert "\n# Heading" not in doc.text
