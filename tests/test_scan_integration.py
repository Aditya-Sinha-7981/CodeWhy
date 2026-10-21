from __future__ import annotations

from pathlib import Path

import pytest

import codewhy.cli as cli
from codewhy.llm.provider import AuthError, ProviderError, TransientError
from tests.helpers import make_project, run_cli, snapshot_tree
from tests.support.fake_provider import FakeProvider


def install_fake(monkeypatch: pytest.MonkeyPatch, fake: FakeProvider) -> None:
    monkeypatch.setattr(cli, "build_provider", lambda config: fake)


def test_scan_writes_default_output_and_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "def main():\n    return 1\n"})
    fake = FakeProvider()
    install_fake(monkeypatch, fake)
    result = run_cli(["scan", str(tmp_path)])
    assert result.exit_code == 0, result.stderr
    assert (tmp_path / "PROJECT_DOCS.md").is_file()
    assert "files, " in result.stdout and "PROJECT_DOCS.md" in result.stdout
    assert fake.preflight_calls == 1


def test_scan_reports_provider_check_before_pipeline_progress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "def main():\n    return 1\n"})
    install_fake(monkeypatch, FakeProvider())
    result = run_cli(["scan", str(tmp_path)])
    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "checking provider fake-provider..."
    assert lines[1] == "provider preflight: started"
    assert any(line.startswith("[1/") for line in lines)


def test_scan_quiet_suppresses_provider_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n"})
    install_fake(monkeypatch, FakeProvider())
    result = run_cli(["scan", str(tmp_path), "--quiet"])
    assert result.exit_code == 0, result.stderr
    assert "checking provider" not in result.stdout


def test_scan_reports_safe_stage_progress_and_generation_boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "secret = 'must not be printed'\n"})
    install_fake(monkeypatch, FakeProvider())
    result = run_cli(["scan", str(tmp_path)])
    assert result.exit_code == 0, result.stderr
    output = result.stdout
    expected = [
        "provider preflight: started",
        "provider preflight: complete",
        "discovery: started",
        "discovery: complete:",
        "extraction: started:",
        "extraction: complete:",
        "graph: started",
        "graph: complete:",
        "file generation: started:",
        "file generation: complete:",
        "module generation: started:",
        "module generation: complete:",
        "project generation: started:",
        "project generation: complete:",
    ]
    positions = [output.index(item) for item in expected]
    assert positions == sorted(positions)
    assert "must not be printed" not in output
    assert "file request main.py: started: attempt 1/2; prompt ~" in output
    assert "file request main.py: complete: attempt 1/2" in output
    assert "project request: started: attempt 1/2; prompt ~" in output


def test_scan_quiet_suppresses_progress_and_both_writes_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n"})
    install_fake(monkeypatch, FakeProvider())
    result = run_cli(["scan", str(tmp_path), "--output", "both", "--quiet"])
    assert result.exit_code == 0, result.stderr
    assert "[1/" not in result.stdout
    assert (tmp_path / "PROJECT_DOCS.md").exists()
    assert (tmp_path / "codewhy-docs" / "PROJECT_OVERVIEW.md").exists()


def test_preflight_failure_happens_before_source_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "main.py"
    make_project(tmp_path, {"main.py": "x = 1\n"})
    fake = FakeProvider(preflight_error=ProviderError("unreachable"))
    install_fake(monkeypatch, fake)
    original = Path.read_bytes

    def fail_source(path: Path):
        if path == source:
            raise AssertionError("source was read before preflight")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", fail_source)
    result = run_cli(["scan", str(tmp_path)])
    assert result.exit_code == 1
    assert fake.calls == []


def test_scan_converts_interrupt_to_exit_130_without_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n"})
    install_fake(monkeypatch, FakeProvider())
    monkeypatch.setattr(cli, "run_pipeline", lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))
    result = run_cli(["scan", str(tmp_path)])
    assert result.exit_code == 130
    assert "scan interrupted; no output was written." in result.stderr
    assert not (tmp_path / "PROJECT_DOCS.md").exists()


def test_remote_without_ack_is_rejected_without_provider_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n"})
    fake = FakeProvider(is_remote=True, endpoint_label="remote.example")
    install_fake(monkeypatch, fake)
    result = run_cli(["scan", str(tmp_path)])
    assert result.exit_code == 1
    assert fake.preflight_calls == 0
    assert fake.calls == []


def test_auth_failure_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n"})
    fake = FakeProvider(errors={0: AuthError("bad auth")})
    install_fake(monkeypatch, fake)
    before = snapshot_tree(tmp_path)
    result = run_cli(["scan", str(tmp_path)])
    assert result.exit_code == 1
    assert snapshot_tree(tmp_path) == before


def test_handwritten_output_refusal_leaves_tree_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n", "PROJECT_DOCS.md": "handwritten\n"})
    install_fake(monkeypatch, FakeProvider())
    before = snapshot_tree(tmp_path)
    result = run_cli(["scan", str(tmp_path)])
    assert result.exit_code == 1
    assert snapshot_tree(tmp_path) == before
    forced = run_cli(["scan", str(tmp_path), "--force"])
    assert forced.exit_code == 0
    assert b"generated_by: codewhy" in (tmp_path / "PROJECT_DOCS.md").read_bytes()


def test_no_supported_files_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"README.md": "hello\n"})
    fake = FakeProvider()
    install_fake(monkeypatch, fake)
    result = run_cli(["scan", str(tmp_path)])
    assert result.exit_code == 1
    assert "No supported source files found" in result.stderr


def test_skipped_and_failed_items_return_exit_two_and_write_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n", "broken.py": "def broken(:\n    pass\n"})
    install_fake(monkeypatch, FakeProvider(errors={0: TransientError("temporary")}))
    result = run_cli(["scan", str(tmp_path)])
    assert result.exit_code == 2
    assert (tmp_path / "PROJECT_DOCS.md").exists()
    assert "[generation failed]" in (tmp_path / "PROJECT_DOCS.md").read_text(encoding="utf-8")
    assert "skipped" in result.stdout
