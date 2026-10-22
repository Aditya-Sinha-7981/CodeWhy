"""Atomic output writes and stale temp cleanup (task 08 §8 `test_atomic_write.py`)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import codewhy.cli as cli
import codewhy.render.writer as writer
from codewhy.render.writer import is_temp_name, remove_stale_temps, write_atomic
from tests.helpers import make_project, run_cli, snapshot_tree
from tests.support.fake_provider import FakeProvider

STALE = "PROJECT_DOCS.md.0123456789abcdef.tmp"


def install(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    fake = FakeProvider()
    monkeypatch.setattr(cli, "build_provider", lambda config: fake)
    return fake


def test_write_atomic_writes_utf8_with_lf(tmp_path: Path) -> None:
    target = tmp_path / "doc.md"
    write_atomic(target, "a — b\r\nc\n")
    assert target.read_bytes() == "a — b\nc\n".encode("utf-8")
    assert os.listdir(tmp_path) == ["doc.md"]


def test_temp_file_lives_next_to_the_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Path, Path]] = []
    real = os.replace

    def spy(source, target):
        seen.append((Path(source), Path(target)))
        real(source, target)

    monkeypatch.setattr(writer.os, "replace", spy)
    (tmp_path / "sub").mkdir()
    write_atomic(tmp_path / "sub" / "doc.md", "x\n")
    [(temp, target)] = seen
    assert temp.parent == target.parent == tmp_path / "sub"
    assert is_temp_name(temp.name) and temp.name.startswith("doc.md.")


@pytest.mark.parametrize("stage", ["write", "replace"])
def test_a_failure_mid_write_keeps_the_previous_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str) -> None:
    target = tmp_path / "doc.md"
    target.write_text("previous\n", encoding="utf-8")
    if stage == "replace":
        monkeypatch.setattr(writer.os, "replace", lambda s, t: (_ for _ in ()).throw(OSError(28, "No space left on device")))
    else:
        monkeypatch.setattr(writer.os, "fsync", lambda fd: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises((OSError, KeyboardInterrupt)):
        write_atomic(target, "new content that must not appear half-written\n" * 100)
    assert target.read_text(encoding="utf-8") == "previous\n"
    assert os.listdir(tmp_path) == ["doc.md"]


def test_temp_name_pattern_is_narrow() -> None:
    assert is_temp_name(STALE)
    for name in ("notes.tmp", "a.0123.tmp", "a.0123456789ABCDEF.tmp", "a.0123456789abcdef.tmp.bak", ".0123456789abcdef.tmp"):
        assert not is_temp_name(name), name


def test_stale_temps_are_removed_on_the_next_scan_and_never_seen_by_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(
        tmp_path,
        {
            "main.py": "x = 1\n",
            STALE: "half a document",
            "codewhy-docs/main.py.md.fedcba9876543210.tmp": "half",
            "notes.tmp": "a user's own file",
        },
    )
    install(monkeypatch)
    result = run_cli(["scan", str(tmp_path), "--quiet", "--output", "both"])
    assert result.exit_code == 0, result.stderr
    assert not (tmp_path / STALE).exists()
    assert not (tmp_path / "codewhy-docs" / "main.py.md.fedcba9876543210.tmp").exists()
    assert (tmp_path / "notes.tmp").read_text(encoding="utf-8") == "a user's own file"
    assert STALE not in (tmp_path / "PROJECT_DOCS.md").read_text(encoding="utf-8")


def test_dry_run_leaves_stale_temps_alone(tmp_path: Path) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n", STALE: "half"})
    assert run_cli(["scan", str(tmp_path), "--dry-run"]).exit_code == 0
    assert (tmp_path / STALE).exists()


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_cleanup_never_follows_symlinks(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / STALE).write_text("not ours to delete", encoding="utf-8")
    root = make_project(tmp_path / "project", {"main.py": "x = 1\n"})
    (root / "codewhy-docs").mkdir()
    try:
        (root / "codewhy-docs" / "link").symlink_to(outside, target_is_directory=True)
        (root / "PROJECT_DOCS.md.aaaaaaaaaaaaaaaa.tmp").symlink_to(outside / STALE)
    except OSError as exc:  # pragma: no cover - Windows without symlink privilege
        pytest.skip(f"cannot create symlinks: {exc}")
    assert remove_stale_temps(root, "codewhy-docs") == []
    assert (outside / STALE).read_text(encoding="utf-8") == "not ours to delete"


def test_docs_tree_writes_nothing_when_any_overwrite_check_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n", "lib.py": "y = 2\n", "codewhy-docs/main.py.md": "hand-written notes\n"})
    fake = install(monkeypatch)
    before = snapshot_tree(tmp_path)
    result = run_cli(["scan", str(tmp_path), "--output", "both"])
    assert result.exit_code == 1
    # The message names the file the OS way (backslashes on Windows).
    assert str(Path("codewhy-docs") / "main.py.md") in result.stderr and "--force" in result.stderr
    assert snapshot_tree(tmp_path) == before  # no PROJECT_DOCS.md, no other tree file, no cache
    assert fake.calls == []
