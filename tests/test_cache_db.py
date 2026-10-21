from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codewhy.cache.db import CacheDB, CacheLocked, CacheReadOnly
from codewhy.summarize.result import PassResult
from codewhy.summarize.schemas import FilePass
from tests.helpers import FIXED_TIME


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
