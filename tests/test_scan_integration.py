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


def test_scan_quiet_suppresses_provider_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n"})
    install_fake(monkeypatch, FakeProvider())
    result = run_cli(["scan", str(tmp_path), "--quiet"])
    assert result.exit_code == 0, result.stderr
    assert "checking provider" not in result.stdout


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
    assert "Interrupted. Progress is saved in the cache; re-run to resume." in result.stderr
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
    # The cache is opened before the first call (task 07), so an empty,
    # self-ignoring .codewhy/ may exist; no output document is written.
    after = {path: data for path, data in snapshot_tree(tmp_path).items() if not path.startswith(".codewhy/")}
    assert after == before


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


# --- review/06 additions -------------------------------------------------------------


def test_existing_codewhy_output_is_overwritten_silently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n", "PROJECT_DOCS.md": "---\ngenerated_by: codewhy v0.0.1\n---\nOLD-CONTENT\n"})
    install_fake(monkeypatch, FakeProvider())
    result = run_cli(["scan", str(tmp_path), "--quiet"])
    assert result.exit_code == 0, result.stderr
    text = (tmp_path / "PROJECT_DOCS.md").read_text(encoding="utf-8")
    assert "OLD-CONTENT" not in text and "## Files" in text


def test_summary_line_counts_and_skip_lines_come_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.fixtures.builders import build_broken_files

    root = build_broken_files(tmp_path)
    (root / "codewhy.toml").write_text("[extraction]\nmax_file_bytes = 64\n", encoding="utf-8")
    install_fake(monkeypatch, FakeProvider())
    result = run_cli(["scan", str(root), "--quiet"])
    assert result.exit_code == 2
    lines = result.stdout.strip().split("\n")
    assert lines[:-1] == [
        "skipped invalid_utf8.py (undecodable)",
        "skipped oversized.py (too_large)",
        "skipped syntax_error.py (SyntaxError: invalid syntax (line 1))",
    ]
    tokens = (root / "PROJECT_DOCS.md").read_text(encoding="utf-8")
    from codewhy.llm.budget import estimate_tokens

    # 4 discovered files (syntax_error.py among them), 3 healthy passes + 1 module + project.
    assert lines[-1] == (
        f"4 files, 0 cached, 5 generated, 0 failed, 3 skipped. PROJECT_DOCS.md ≈ {estimate_tokens(tokens):,} tokens (level 0)."
    )


def test_over_budget_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n", "codewhy.toml": "[output]\ntoken_budget = 10\n"})
    install_fake(monkeypatch, FakeProvider())
    result = run_cli(["scan", str(tmp_path), "--quiet"])
    assert result.exit_code == 0
    assert "(level 2)" in result.stdout
    assert "warning: Level 2 output exceeds the configured token budget." in result.stderr


def test_second_scan_does_not_list_its_own_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n", "notes.txt": "keep\n"})
    install_fake(monkeypatch, FakeProvider())
    for _ in range(2):
        assert run_cli(["scan", str(tmp_path), "--quiet", "--output", "both"]).exit_code == 0
    text = (tmp_path / "PROJECT_DOCS.md").read_text(encoding="utf-8")
    others = text.split("### Other files in this project\n\n", 1)[1].split("\n\n", 1)[0]
    assert others == "- `notes.txt`"


def test_gitignore_is_never_read_and_nothing_is_written_outside_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    make_project(root, {"main.py": "x = 1\n", "hidden.py": "y = 2\n", ".gitignore": "hidden.py\n"})
    install_fake(monkeypatch, FakeProvider())
    outside_before = sorted(p.name for p in tmp_path.iterdir())
    source_before = {name: (root / name).read_bytes() for name in ("main.py", "hidden.py", ".gitignore")}
    result = run_cli(["scan", str(root), "--quiet", "--output", "both"])
    assert result.exit_code == 0, result.stderr
    assert "### `hidden.py`" in (root / "PROJECT_DOCS.md").read_text(encoding="utf-8")
    assert sorted(p.name for p in tmp_path.iterdir()) == outside_before
    assert {name: (root / name).read_bytes() for name in source_before} == source_before
    written = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} - set(source_before)
    assert all(
        path == "PROJECT_DOCS.md" or path.startswith("codewhy-docs/") or path.startswith(".codewhy/")
        for path in written
    )


def test_docs_tree_mode_writes_only_the_tree_to_codewhy_docs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n", "pkg/mod.py": "y = 2\n"})
    install_fake(monkeypatch, FakeProvider())
    result = run_cli(["scan", str(tmp_path), "--quiet", "--output", "docs-tree"])
    assert result.exit_code == 0, result.stderr
    assert not (tmp_path / "PROJECT_DOCS.md").exists()
    written = sorted(p.relative_to(tmp_path).as_posix() for p in (tmp_path / "codewhy-docs").rglob("*.md"))
    assert written == [
        "codewhy-docs/PROJECT_OVERVIEW.md",
        "codewhy-docs/_module.md",
        "codewhy-docs/main.py.md",
        "codewhy-docs/pkg/_module.md",
        "codewhy-docs/pkg/mod.py.md",
    ]


def test_provider_and_model_options_reach_the_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n"})
    seen = []

    def factory(config):
        seen.append((config.llm.provider, config.llm.model))
        return FakeProvider()

    monkeypatch.setattr(cli, "build_provider", factory)
    result = run_cli(["scan", str(tmp_path), "--provider", "gemini", "--model", "gem-x", "--quiet"])
    assert result.exit_code == 0, result.stderr
    assert seen == [("gemini", "gem-x")]


def test_all_failed_run_still_writes_placeholders_and_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"main.py": "x = 1\n"})
    install_fake(monkeypatch, FakeProvider(respond=lambda call: TransientError("down")))
    result = run_cli(["scan", str(tmp_path), "--quiet"])
    assert result.exit_code == 2
    assert ", 0 cached, 0 generated, 3 failed, 0 skipped." in result.stdout
    assert (tmp_path / "PROJECT_DOCS.md").read_text(encoding="utf-8").count("[generation failed]") >= 3


def test_progress_is_reported_as_each_item_finishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {"a.py": "x = 1\n", "b.py": "y = 2\n", "c.py": "z = 3\n"})
    events: list[str] = []
    fake = FakeProvider(respond=lambda call: events.append("call") or {k: "Text." for k in call.schema["required"]})
    install_fake(monkeypatch, fake)
    monkeypatch.setattr(cli.typer, "echo", lambda message="", **kw: events.append(str(message)))
    run_cli(["scan", str(tmp_path)])
    sequence = [e if e == "call" else "item" for e in events if e == "call" or e.startswith("[")]
    assert sequence == ["call", "item"] * 5


def test_an_auth_error_stops_the_run_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_project(tmp_path, {f"f{n}.py": f"x = {n}\n" for n in range(6)})
    fake = FakeProvider(errors={0: AuthError("bad key")})
    install_fake(monkeypatch, fake)
    result = run_cli(["scan", str(tmp_path)])
    assert result.exit_code == 1
    assert "bad key" in result.stderr
    assert len(fake.calls) == 1
