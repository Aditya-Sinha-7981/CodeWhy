"""Input and environment edge cases (task 08 §8 `test_cli_edge_cases.py`)."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

import codewhy.cli as cli
from tests.helpers import make_project, run_cli, snapshot_tree
from tests.support.fake_provider import FakeProvider

posix_permissions = pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="needs POSIX permission bits (Windows ACLs differ; root ignores them)",
)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    provider = FakeProvider()
    monkeypatch.setattr(cli, "build_provider", lambda config: provider)
    return provider


def test_nonexistent_path(tmp_path: Path, fake: FakeProvider) -> None:
    missing = tmp_path / "no such folder"
    result = run_cli(["scan", str(missing)])
    assert result.exit_code == 1
    assert result.stderr.strip() == f"error: path does not exist: {missing}"
    assert fake.preflight_calls == 0


def test_path_is_a_file(tmp_path: Path, fake: FakeProvider) -> None:
    file = make_project(tmp_path, {"main.py": "x = 1\n"}) / "main.py"
    result = run_cli(["scan", str(file)])
    assert result.exit_code == 1
    assert "expected a folder" in result.stderr and str(file) in result.stderr


def test_spaces_and_unicode_in_paths_work_end_to_end(tmp_path: Path, fake: FakeProvider) -> None:
    root = make_project(
        tmp_path / "mon projet ü",
        {"main file.py": "from naïve import go\n\ngo()\n", "naïve.py": "def go():\n    return 1\n", "sous dossier/ç.py": "Z = 1\n"},
    )
    result = run_cli(["scan", str(root), "--output", "both"])
    assert result.exit_code == 0, result.stderr
    text = (root / "PROJECT_DOCS.md").read_text(encoding="utf-8")
    assert "# mon projet ü — codebase documentation" in text
    assert "### `main file.py`" in text and "- `naïve.py`" in text
    assert (root / "codewhy-docs" / "sous dossier" / "ç.py.md").is_file()
    assert "[1/" in result.stdout and "naïve.py" in result.stdout


@posix_permissions
def test_read_only_project_folder(tmp_path: Path, fake: FakeProvider) -> None:
    root = make_project(tmp_path / "project", {"main.py": "x = 1\n"})
    before = snapshot_tree(root)
    root.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = run_cli(["scan", str(root)])
    finally:
        root.chmod(stat.S_IRWXU)
    assert result.exit_code == 1
    assert str(root / ".codewhy") in result.stderr
    assert snapshot_tree(root) == before and fake.calls == []


@posix_permissions
def test_unwritable_output_folder_writes_nothing_partial(tmp_path: Path, fake: FakeProvider) -> None:
    root = make_project(tmp_path / "project", {"main.py": "x = 1\n", "codewhy-docs/notes.txt": "mine\n"})
    docs = root / "codewhy-docs"
    docs.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = run_cli(["scan", str(root), "--output", "both"])
    finally:
        docs.chmod(stat.S_IRWXU)
    assert result.exit_code == 1
    assert f"cannot write to {docs.resolve()}" in result.stderr
    assert not (root / "PROJECT_DOCS.md").exists()  # nothing written, not even the single file
    assert sorted(os.listdir(docs)) == ["notes.txt"]


def test_invalid_config_lists_every_problem(tmp_path: Path, fake: FakeProvider) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n", "codewhy.toml": "[llm]\ntemperature = 9\n[extraction]\nmax_file_bytes = -1\n"})
    result = run_cli(["scan", str(tmp_path)])
    assert result.exit_code == 1
    assert "(2 problems)" in result.stderr
    assert "[llm].temperature" in result.stderr and "[extraction].max_file_bytes" in result.stderr
    assert "Traceback" not in result.stderr and fake.preflight_calls == 0


def test_file_deleted_between_discovery_and_reading_is_an_unreadable_skip(
    tmp_path: Path, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n", "gone.py": "y = 2\n"})
    real = Path.read_bytes

    def vanishing(self: Path) -> bytes:
        if self.name == "gone.py":
            self.unlink()  # listed by the directory scan, deleted before it is read
            return real(self)
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", vanishing)
    result = run_cli(["scan", str(tmp_path)])
    assert result.exit_code == 2
    assert "skipped gone.py (unreadable)" in result.stdout
    assert "Traceback" not in result.stderr
    assert "### `main.py`" in (tmp_path / "PROJECT_DOCS.md").read_text(encoding="utf-8")


def test_deep_trees_and_long_names_do_not_crash(tmp_path: Path, fake: FakeProvider) -> None:
    deep = "/".join(f"level{n:02d}" for n in range(40))
    long_name = "a_rather_long_module_name_" * 4 + ".py"
    root = make_project(tmp_path, {f"{deep}/{long_name}": "VALUE = 1\n", "main.py": "x = 1\n"})
    result = run_cli(["scan", str(root), "--quiet"])
    assert result.exit_code == 0, result.stderr
    assert f"`{deep}/{long_name}`" in (root / "PROJECT_DOCS.md").read_text(encoding="utf-8")


def boom(*args, **kwargs):
    raise RuntimeError("detail that could quote private text")


def test_unexpected_errors_are_short_by_default(tmp_path: Path, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n"})
    monkeypatch.delenv("CODEWHY_DEBUG", raising=False)
    monkeypatch.setattr(cli, "render_single_file", boom)
    result = run_cli(["scan", str(tmp_path)])
    assert result.exit_code == 1
    assert result.stderr.strip() == (
        "error: internal error (RuntimeError). Re-run with CODEWHY_DEBUG=1 to see the traceback, and please report it."
    )
    assert not (tmp_path / "PROJECT_DOCS.md").exists()


def test_codewhy_debug_shows_the_traceback(tmp_path: Path, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n"})
    monkeypatch.setenv("CODEWHY_DEBUG", "1")
    monkeypatch.setattr(cli, "render_single_file", boom)
    result = run_cli(["scan", str(tmp_path)])
    assert result.exit_code == 1
    assert "Traceback (most recent call last)" in result.stderr and "RuntimeError" in result.stderr
    assert result.stderr.rstrip().endswith("please report it.")


def test_other_commands_are_guarded_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n"})
    monkeypatch.delenv("CODEWHY_DEBUG", raising=False)
    monkeypatch.setattr(cli, "build_project", boom)
    result = run_cli(["inspect", str(tmp_path)])
    assert result.exit_code == 1 and "internal error (RuntimeError)" in result.stderr
    assert "Traceback" not in result.stderr
