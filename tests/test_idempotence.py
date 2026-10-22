from __future__ import annotations

from pathlib import Path

import pytest

import codewhy.cli as cli
from tests.helpers import make_project, run_cli
from tests.support.fake_provider import FakeProvider


@pytest.mark.parametrize("mode", ["single-file", "docs-tree", "both"])
def test_unchanged_cli_runs_are_byte_identical_and_zero_call(tmp_path: Path, monkeypatch, mode: str) -> None:
    make_project(tmp_path, {"main.py": "def main():\n    return 1\n"})
    first_provider = FakeProvider()
    monkeypatch.setattr(cli, "build_provider", lambda config: first_provider)
    assert run_cli(["scan", str(tmp_path), "--quiet", "--output", mode]).exit_code == 0
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file() and path.name != "cache.db"
    }
    second_provider = FakeProvider()
    monkeypatch.setattr(cli, "build_provider", lambda config: second_provider)
    result = run_cli(["scan", str(tmp_path), "--quiet", "--output", mode])
    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file() and path.name != "cache.db"
    }
    assert result.exit_code == 0, result.stderr
    assert second_provider.calls == []
    assert before == after


def test_one_changed_file_changes_only_the_documents_behind_rerun_passes(tmp_path: Path) -> None:
    from datetime import timedelta

    from codewhy.config import Config
    from codewhy.pipeline import run_pipeline
    from codewhy.project import build_project
    from codewhy.render.docs_tree import render_docs_tree
    from codewhy.render.single_file import render_single_file
    from tests.helpers import FIXED_TIME

    make_project(
        tmp_path,
        {
            "main.py": "from pkg.a import run\n\nrun()\n",
            "pkg/a.py": "from pkg.b import helper\n\n\ndef run():\n    return helper()\n",
            "pkg/b.py": "def helper():\n    return 1\n",
        },
    )
    config = Config()
    later = FIXED_TIME + timedelta(days=1)
    project = build_project(tmp_path, config)
    first = run_pipeline(project, FakeProvider(), config, clock=lambda: FIXED_TIME)
    before = render_docs_tree(project, first, config, version="0")

    # FakeProvider's text hashes the prompt, so b's Purpose changes, then pkg's
    # Responsibility, then the project text: those three documents re-run.
    (tmp_path / "pkg" / "b.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
    project = build_project(tmp_path, config)
    second = run_pipeline(project, FakeProvider(), config, clock=lambda: later)
    after = render_docs_tree(project, second, config, version="0")

    rerun = {"pkg/b.py.md", "pkg/_module.md", "PROJECT_OVERVIEW.md"}
    assert second.counts["generated"] == len(rerun)
    assert {name for name in after if after[name] != before[name]} == rerun
    for name, text in after.items():
        stamp = "2026-01-02T12:00:00Z" if name in rerun else "2026-01-01T12:00:00Z"
        assert f"\ngenerated_at: {stamp}\n" in text, name
    header = render_single_file(project, second, config, version="0").text
    assert "\ngenerated_at: 2026-01-02T12:00:00Z\n" in header.split("\n---\n", 1)[0] + "\n"


def test_generated_at_comes_from_the_cache_not_the_second_runs_clock(tmp_path: Path, monkeypatch) -> None:
    from datetime import datetime

    from tests.helpers import FIXED_TIME

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return FIXED_TIME

    make_project(tmp_path, {"main.py": "def main():\n    return 1\n"})
    monkeypatch.setattr(cli, "build_provider", lambda config: FakeProvider())
    monkeypatch.setattr(cli, "datetime", Frozen)
    assert run_cli(["scan", str(tmp_path), "--quiet"]).exit_code == 0
    first = (tmp_path / "PROJECT_DOCS.md").read_bytes()
    assert b"\ngenerated_at: 2026-01-01T12:00:00Z\n" in first

    monkeypatch.setattr(cli, "datetime", datetime)  # the second run sees the real clock
    second_provider = FakeProvider()
    monkeypatch.setattr(cli, "build_provider", lambda config: second_provider)
    result = run_cli(["scan", str(tmp_path)])
    assert result.exit_code == 0, result.stderr
    assert second_provider.calls == []
    assert (tmp_path / "PROJECT_DOCS.md").read_bytes() == first
    assert "(cached)" in result.stdout and "(generated)" not in result.stdout
    assert "1 files, 3 cached, 0 generated, 0 failed, 0 skipped." in result.stdout
