from __future__ import annotations

import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

from codewhy.cache.db import CacheDB, CacheLocked, CacheReadOnly
from codewhy.config import Config
from codewhy.pipeline import run_pipeline
from codewhy.project import build_project
from codewhy.summarize.result import PassResult
from codewhy.summarize.schemas import FilePass, ModulePass, ProjectPass
from tests.helpers import FIXED_TIME, make_project, run_cli, snapshot_tree
from tests.support.fake_provider import FakeProvider


def test_round_trip_miss_peek_and_gitignore(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / ".codewhy" / "cache.db")
    result = PassResult.ok(FilePass(purpose="Purpose", notes=None), FIXED_TIME)
    db.store("file", "main.py", "key", result)
    hit = db.lookup("file", "main.py", "key")
    assert hit == result
    assert db.peek("file", "main.py", "key")
    assert not db.peek("file", "main.py", "other")
    assert db.lookup("file", "main.py", "other") is None
    assert (tmp_path / ".codewhy" / ".gitignore").read_text() == "*\n"
    db.close()


def test_invalid_row_is_a_miss_and_delete_missing(tmp_path: Path) -> None:
    path = tmp_path / ".codewhy" / "cache.db"
    db = CacheDB(path)
    db.store("file", "keep.py", "key", PassResult.ok(FilePass(purpose="Purpose", notes=None), FIXED_TIME))
    db.close()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE pass_cache SET result_json = ? WHERE subject = 'keep.py'", ("{}",))
    db = CacheDB(path)
    assert db.lookup("file", "keep.py", "key") is None
    db.store("file", "keep.py", "key", PassResult.ok(FilePass(purpose="New", notes=None), FIXED_TIME))
    db.store("file", "gone.py", "key", PassResult.ok(FilePass(purpose="Gone", notes=None), FIXED_TIME))
    assert db.delete_missing("file", {"keep.py"}) == 1
    db.close()


@pytest.mark.parametrize("payload", [b"garbage", None])
def test_corrupt_or_unknown_schema_rebuilds(tmp_path: Path, payload: bytes | None) -> None:
    path = tmp_path / ".codewhy" / "cache.db"
    path.parent.mkdir()
    if payload is not None:
        path.write_bytes(payload)
    else:
        db = CacheDB(path)
        db.store("file", "x.py", "k", PassResult.ok(FilePass(purpose="x", notes=None), FIXED_TIME))
        db.close()
        connection = sqlite3.connect(path)
        try:
            connection.execute("UPDATE meta SET value = '999' WHERE key = 'schema_version'")
            connection.commit()
        finally:
            connection.close()
    db = CacheDB(path)
    assert db.lookup("file", "x.py", "k") is None
    db.store("file", "x.py", "k", PassResult.ok(FilePass(purpose="new", notes=None), FIXED_TIME))
    db.close()


def test_locked_database_has_actionable_error(tmp_path: Path) -> None:
    path = tmp_path / ".codewhy" / "cache.db"
    first = CacheDB(path)
    first.store("file", "x.py", "k", PassResult.ok(FilePass(purpose="x", notes=None), FIXED_TIME))
    second_connection = sqlite3.connect(path)
    second_connection.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(CacheLocked, match="Another codewhy run seems to be using"):
            first.store("file", "x.py", "k2", PassResult.ok(FilePass(purpose="y", notes=None), FIXED_TIME))
    finally:
        second_connection.rollback()
        second_connection.close()
        first.close()


def test_initial_open_lock_does_not_rebuild_database(tmp_path: Path) -> None:
    path = tmp_path / ".codewhy" / "cache.db"
    seed = CacheDB(path)
    seed.store("file", "x.py", "key", PassResult.ok(FilePass(purpose="x", notes=None), FIXED_TIME))
    seed.close()
    before = path.read_bytes()

    locked_connection = sqlite3.connect(path)
    locked_connection.execute("BEGIN EXCLUSIVE")
    try:
        opening = CacheDB(path)
        with pytest.raises(CacheLocked, match="Another codewhy run seems to be using"):
            opening.lookup("file", "x.py", "key")
        opening.close()
        assert path.read_bytes() == before
    finally:
        locked_connection.rollback()
        locked_connection.close()


def test_cache_path_inside_file_is_clear_read_only_error(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    db = CacheDB(blocked / "cache.db")
    with pytest.raises(CacheReadOnly, match="cannot write cache"):
        db.store("file", "x.py", "k", PassResult.ok(FilePass(purpose="x", notes=None), FIXED_TIME))


def test_module_and_project_results_revive_into_their_models(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / ".codewhy" / "cache.db")
    module = PassResult.ok(ModulePass(responsibility="R.", how_pieces_cooperate="C."), FIXED_TIME)
    project = PassResult.ok(ProjectPass(overview="O.", architecture="A."), FIXED_TIME)
    db.store("module", "pkg", "k", module)
    db.store("project", "", "k", project)
    assert db.lookup("module", "pkg", "k") == module
    assert isinstance(db.lookup("project", "", "k").data, ProjectPass)
    db.close()


def test_row_from_a_changed_schema_is_a_miss_and_is_overwritten(tmp_path: Path) -> None:
    path = tmp_path / ".codewhy" / "cache.db"
    db = CacheDB(path)
    # A file-pass payload stored under the module pass cannot revive as ModulePass.
    db.store("module", "pkg", "k", PassResult.ok(FilePass(purpose="Wrong shape."), FIXED_TIME))
    assert db.lookup("module", "pkg", "k") is None
    fresh = PassResult.ok(ModulePass(responsibility="R.", how_pieces_cooperate="C."), FIXED_TIME)
    db.store("module", "pkg", "k", fresh)
    assert db.lookup("module", "pkg", "k") == fresh
    db.close()


def test_failed_results_are_never_stored(tmp_path: Path) -> None:
    db = CacheDB(tmp_path / ".codewhy" / "cache.db")
    db.store("file", "x.py", "k", PassResult.failed("provider unavailable", FIXED_TIME))
    assert db.lookup("file", "x.py", "k") is None
    db.close()
    assert not (tmp_path / ".codewhy").exists()


def test_peek_has_no_side_effects(tmp_path: Path) -> None:
    path = tmp_path / ".codewhy" / "cache.db"
    assert CacheDB(path).peek("file", "x.py", "k") is False
    assert not (tmp_path / ".codewhy").exists()
    db = CacheDB(path)
    db.store("file", "x.py", "k", PassResult.ok(FilePass(purpose="x"), FIXED_TIME))
    db.close()
    before = snapshot_tree(tmp_path)
    reader = CacheDB(path)
    assert reader.peek("file", "x.py", "k") and not reader.peek("file", "x.py", "other")
    reader.close()
    assert snapshot_tree(tmp_path) == before


read_only_dirs = pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX permission bits; root ignores them",
)


@read_only_dirs
def test_read_only_project_fails_before_any_provider_call(tmp_path: Path) -> None:
    root = make_project(tmp_path / "project", {"main.py": "def main():\n    return 1\n"})
    config = Config()
    project = build_project(root, config)
    fake = FakeProvider()
    root.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(CacheReadOnly, match="cannot write cache"):
            run_pipeline(project, fake, config, clock=lambda: FIXED_TIME)
    finally:
        root.chmod(stat.S_IRWXU)
    assert fake.calls == []


@read_only_dirs
def test_read_only_project_scan_exits_1_with_a_clear_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import codewhy.cli as cli

    root = make_project(tmp_path / "project", {"main.py": "def main():\n    return 1\n"})
    fake = FakeProvider()
    monkeypatch.setattr(cli, "build_provider", lambda config: fake)
    root.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = run_cli(["scan", str(root), "--quiet"])
    finally:
        root.chmod(stat.S_IRWXU)
    assert result.exit_code == 1
    assert "cannot write cache directory" in result.stderr
    assert fake.calls == []
