from __future__ import annotations

from pathlib import Path

import pytest

from codewhy.config import Config
from codewhy.pipeline import run_pipeline
from codewhy.project import build_project
from codewhy.render.docs_tree import OutputError, render_docs_tree, write_outputs
from tests.helpers import FIXED_TIME
from tests.support.fake_provider import FakeProvider

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"


def tree_result():
    config = Config()
    project = build_project(FIXTURES / "flat_scripts", config)
    result = run_pipeline(project, FakeProvider(), config, clock=lambda: FIXED_TIME)
    return project, result, config


def test_tree_mirrors_sources_and_has_frontmatter(tmp_path: Path) -> None:
    project, result, config = tree_result()
    tree = render_docs_tree(project, result, config, version="test")
    assert "PROJECT_OVERVIEW.md" in tree
    assert "_module.md" in tree
    assert "main.py.md" in tree
    assert tree["main.py.md"].startswith("---\ngenerated_by: codewhy vtest")
    assert "# `main.py`" in tree["main.py.md"]
    write_outputs(tmp_path, {f"codewhy-docs/{key}": value for key, value in tree.items()}, output_dir="codewhy-docs")
    assert (tmp_path / "codewhy-docs" / "main.py.md").read_bytes().endswith(b"\n")
    assert tree["PROJECT_OVERVIEW.md"] == (GOLDEN / "flat_scripts_tree_overview.md").read_text(encoding="utf-8")


def test_handwritten_target_is_refused_without_partial_write(tmp_path: Path) -> None:
    project, result, config = tree_result()
    tree = render_docs_tree(project, result, config, version="test")
    (tmp_path / "PROJECT_DOCS.md").write_text("mine\n", encoding="utf-8")
    try:
        write_outputs(tmp_path, {"PROJECT_DOCS.md": "generated\n", "other.md": tree["PROJECT_OVERVIEW.md"]})
    except OutputError:
        pass
    else:
        raise AssertionError("expected overwrite refusal")
    assert (tmp_path / "other.md").exists() is False


def test_stale_cleanup_only_removes_codewhy_documents(tmp_path: Path) -> None:
    project, result, config = tree_result()
    tree_dir = tmp_path / "codewhy-docs"
    tree_dir.mkdir()
    (tree_dir / "deleted.py.md").write_text("---\ngenerated_by: codewhy vtest\n---\nold\n", encoding="utf-8")
    (tree_dir / "handwritten.md").write_text("mine\n", encoding="utf-8")
    write_outputs(tmp_path, {f"codewhy-docs/{key}": value for key, value in render_docs_tree(project, result, config, version="test").items()}, output_dir="codewhy-docs")
    assert not (tree_dir / "deleted.py.md").exists()
    assert (tree_dir / "handwritten.md").exists()


def test_output_traversal_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(OutputError):
        write_outputs(tmp_path, {"../outside.md": "bad\n"})
