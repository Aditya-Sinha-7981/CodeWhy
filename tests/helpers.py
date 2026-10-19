"""Shared test helpers (docs/TESTS.md -> Helpers)."""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Iterator, Mapping

from typer.testing import CliRunner

from codewhy.cli import app

FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def make_project(tmp_path: Path, files: Mapping[str, str | bytes]) -> Path:
    """Create files (POSIX relative paths) under tmp_path and return tmp_path.

    str content is written as UTF-8 without newline translation; bytes as-is.
    """
    for rel, content in files.items():
        target = tmp_path.joinpath(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        target.write_bytes(data)
    return tmp_path


@dataclass(frozen=True)
class CliResult:
    exit_code: int
    stdout: str
    stderr: str


@contextmanager
def _chdir(path: Path | None) -> Iterator[None]:
    if path is None:
        yield
        return
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def run_cli(args: list[str], cwd: Path | None = None) -> CliResult:
    """Invoke the codewhy CLI in-process; stdout and stderr are captured separately."""
    try:
        runner = CliRunner(mix_stderr=False)  # type: ignore[call-arg]  # older click
    except TypeError:
        runner = CliRunner()
    with _chdir(cwd):
        result = runner.invoke(app, args, catch_exceptions=False)
    return CliResult(exit_code=result.exit_code, stdout=result.stdout, stderr=result.stderr)


def fixed_clock() -> datetime:
    """Deterministic clock for tests; injected wherever code takes a `clock`."""
    return FIXED_TIME


def snapshot_tree(root: Path) -> dict[str, bytes | None]:
    """Every path under root (dirs map to None) with file contents, for no-write checks."""
    snap: dict[str, bytes | None] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath)
        for name in dirnames:
            snap[(base / name).relative_to(root).as_posix() + "/"] = None
        for name in filenames:
            path = base / name
            snap[path.relative_to(root).as_posix()] = path.read_bytes()
    return snap
