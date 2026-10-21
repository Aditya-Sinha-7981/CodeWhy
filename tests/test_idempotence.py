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
