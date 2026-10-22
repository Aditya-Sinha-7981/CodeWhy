"""Exit codes and interrupts (task 08 §8 `test_exit_codes.py`)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

import codewhy.cli as cli
from codewhy.llm.provider import TransientError
from codewhy.pipeline import RunResult
from codewhy.progress import EXIT_INTERRUPTED, exit_code_for
from tests.helpers import make_project, run_cli, snapshot_tree
from tests.support.fake_provider import FakeProvider

PROJECT = {
    "main.py": "from pkg.a import run\n\nrun()\n",
    "pkg/a.py": "from pkg.b import helper\n\n\ndef run():\n    return helper()\n",
    "pkg/b.py": "def helper():\n    return 1\n",
}
PASSES = 3 + 2 + 1  # files, modules (".", "pkg"), project


def result_with(failed: int = 0) -> RunResult:
    return RunResult(files={}, modules={}, project=None, counts={"generated": 1, "cached": 0, "failed": failed, "skipped": 0})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("run_result", "skips", "fatal", "expected"),
    [
        (result_with(), [], False, 0),
        (result_with(), [("a.py", "too_large")], False, 2),
        (result_with(failed=1), [], False, 2),
        (result_with(failed=2), [("a.py", "undecodable")], False, 2),
        (result_with(), [], True, 1),
        (result_with(failed=1), [("a.py", "unreadable")], True, 1),
        (None, [], False, 1),
    ],
)
def test_exit_code_table(run_result, skips, fatal, expected) -> None:
    assert exit_code_for(run_result, skips, fatal) == expected


def install(monkeypatch: pytest.MonkeyPatch, fake: FakeProvider) -> FakeProvider:
    monkeypatch.setattr(cli, "build_provider", lambda config: fake)
    return fake


def test_real_scans_exit_0_1_and_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clean = make_project(tmp_path / "clean", PROJECT)
    install(monkeypatch, FakeProvider())
    assert run_cli(["scan", str(clean), "--quiet"]).exit_code == 0

    failing = make_project(tmp_path / "failing", PROJECT)
    install(monkeypatch, FakeProvider(errors={0: TransientError("down")}))
    assert run_cli(["scan", str(failing), "--quiet"]).exit_code == 2

    skipping = make_project(tmp_path / "skipping", {**PROJECT, "bad.py": "def bad(:\n"})
    install(monkeypatch, FakeProvider())
    assert run_cli(["scan", str(skipping), "--quiet"]).exit_code == 2

    empty = make_project(tmp_path / "empty", {"notes.txt": "nothing\n"})
    assert run_cli(["scan", str(empty), "--quiet"]).exit_code == 1


def cached_rows(root: Path) -> int:
    connection = sqlite3.connect(root / ".codewhy" / "cache.db")
    try:
        return connection.execute("SELECT count(*) FROM pass_cache").fetchone()[0]
    finally:
        connection.close()


@pytest.mark.parametrize("interrupt_at", [0, 2, PASSES - 1])
def test_interrupt_exits_130_keeps_cache_and_previous_output_then_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupt_at: int
) -> None:
    root = make_project(tmp_path, PROJECT)
    (root / "codewhy.toml").write_text("[llm]\nseed = 1\n", encoding="utf-8")
    install(monkeypatch, FakeProvider())
    assert run_cli(["scan", str(root), "--quiet"]).exit_code == 0
    previous = (root / "PROJECT_DOCS.md").read_bytes()

    # A new seed changes every key, so every pass must run again.
    (root / "codewhy.toml").write_text("[llm]\nseed = 2\n", encoding="utf-8")
    interrupted = install(monkeypatch, FakeProvider(errors={interrupt_at: KeyboardInterrupt()}))
    result = run_cli(["scan", str(root)])
    assert result.exit_code == EXIT_INTERRUPTED
    assert result.stderr.strip() == "Interrupted. Progress is saved in the cache; re-run to resume."
    assert "Traceback" not in result.stderr
    assert len(interrupted.calls) == interrupt_at + 1
    assert (root / "PROJECT_DOCS.md").read_bytes() == previous  # untouched, not half-written
    assert not [p for p in snapshot_tree(root) if p.endswith(".tmp")]
    assert cached_rows(root) == PASSES  # old rows for the remaining passes plus the new ones, no cleanup ran

    resumed = install(monkeypatch, FakeProvider())
    again = run_cli(["scan", str(root), "--quiet"])
    assert again.exit_code == 0, again.stderr
    assert len(resumed.calls) == PASSES - interrupt_at
    assert f"{interrupt_at} cached, {PASSES - interrupt_at} generated" in again.stdout


def test_interrupt_while_writing_leaves_previous_output_and_no_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import codewhy.render.writer as writer

    root = make_project(tmp_path, PROJECT)
    install(monkeypatch, FakeProvider())
    assert run_cli(["scan", str(root), "--quiet"]).exit_code == 0
    previous = (root / "PROJECT_DOCS.md").read_bytes()
    (root / "pkg" / "b.py").write_text("def helper():\n    return 2\n", encoding="utf-8")

    def interrupted_replace(source, target):
        raise KeyboardInterrupt()

    monkeypatch.setattr(writer.os, "replace", interrupted_replace)
    install(monkeypatch, FakeProvider())
    result = run_cli(["scan", str(root), "--quiet"])
    assert result.exit_code == EXIT_INTERRUPTED
    assert (root / "PROJECT_DOCS.md").read_bytes() == previous
    assert not [p for p in snapshot_tree(root) if p.endswith(".tmp")]


def test_unchanged_rerun_after_interrupt_is_fully_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_project(tmp_path, PROJECT)
    install(monkeypatch, FakeProvider(errors={3: KeyboardInterrupt()}))
    assert run_cli(["scan", str(root), "--quiet"]).exit_code == EXIT_INTERRUPTED
    assert not (root / "PROJECT_DOCS.md").exists()
    first = install(monkeypatch, FakeProvider())
    assert run_cli(["scan", str(root), "--quiet"]).exit_code == 0
    assert len(first.calls) == PASSES - 3
    later = install(monkeypatch, FakeProvider())
    monkeypatch.setattr(cli, "datetime", type("Later", (datetime,), {"now": classmethod(lambda cls, tz=None: datetime(2030, 1, 1, tzinfo=UTC))}))
    before = (root / "PROJECT_DOCS.md").read_bytes()
    assert run_cli(["scan", str(root), "--quiet"]).exit_code == 0
    assert later.calls == [] and (root / "PROJECT_DOCS.md").read_bytes() == before
