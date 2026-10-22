"""`scan --dry-run` (task 08 §8): provider-free, read-only, and exact for file passes."""

from __future__ import annotations

from pathlib import Path

import pytest

import codewhy.cli as cli
from codewhy.cache.db import CacheDB
from codewhy.config import Config
from codewhy.llm.budget import estimate_tokens
from codewhy.pipeline import run_pipeline
from codewhy.plan import plan_scan
from codewhy.project import build_project
from tests.fixtures.builders import OVERSIZED_FILE, build_broken_files, build_oversized_file
from tests.helpers import FIXED_TIME, make_project, run_cli, snapshot_tree
from tests.support.fake_provider import Call, FakeProvider

SMALL_CONTEXT = "[llm]\ncontext_tokens = 2400\nmax_output_tokens = 256\n"
PROJECT = {
    "main.py": "from pkg.a import run\n\nrun()\n",
    "pkg/a.py": "from pkg.b import helper\n\n\ndef run():\n    return helper()\n",
    "pkg/b.py": "def helper():\n    return 1\n",
    "pkg/empty.py": "# only a comment\n",
}


@pytest.fixture
def no_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(config):  # pragma: no cover - reaching this fails the test
        raise AssertionError("--dry-run must not build a provider")

    monkeypatch.setattr(cli, "build_provider", refuse)


def kind(call: Call) -> str:
    if "## Project\n" in call.prompt:
        return "project"
    if "## Module\npath: " in call.prompt:
        return "module"
    return "file"


def real_run(root: Path, config: Config) -> FakeProvider:
    fake = FakeProvider()
    run_pipeline(build_project(root, config), fake, config, clock=lambda: FIXED_TIME)
    return fake


def plan_for(root: Path, config: Config):
    project = build_project(root, config)
    return plan_scan(project, config, CacheDB(project.root / ".codewhy" / "cache.db"))


def passes(plan) -> dict[str, object]:
    return {p.name: p for p in plan.passes}


def test_dry_run_builds_no_provider_and_writes_nothing(tmp_path: Path, no_provider) -> None:
    make_project(tmp_path, PROJECT)
    before = snapshot_tree(tmp_path)
    result = run_cli(["scan", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0, result.stderr
    assert snapshot_tree(tmp_path) == before
    assert not (tmp_path / ".codewhy").exists()
    assert "Dry run: no model calls, nothing written." in result.stdout


def test_dry_run_never_prompts_for_a_remote_endpoint(tmp_path: Path, no_provider) -> None:
    make_project(tmp_path, PROJECT)
    (tmp_path / "codewhy.toml").write_text('[llm]\nprovider = "gemini"\nmodel = "some-model"\n', encoding="utf-8")
    result = run_cli(["scan", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0, result.stderr
    assert "Your source code will be sent" not in result.stdout + result.stderr
    plan = plan_for(tmp_path, cli.load_or_fail(tmp_path))
    hint = f"This would send ≈ {plan.tokens:,} tokens of your source to generativelanguage.googleapis.com."
    assert hint in result.stdout


def test_local_ollama_has_no_remote_hint(tmp_path: Path, no_provider) -> None:
    make_project(tmp_path, PROJECT)
    result = run_cli(["scan", str(tmp_path), "--dry-run"])
    assert "This would send" not in result.stdout


def test_file_pass_numbers_equal_a_real_run_including_chunked_files(tmp_path: Path) -> None:
    root = build_oversized_file(tmp_path, chunk_tokens=600)
    (root / "codewhy.toml").write_text(SMALL_CONTEXT, encoding="utf-8")
    config = cli.load_or_fail(root)
    whole = plan_for(root, config)
    plan = passes(whole)
    fake = real_run(root, config)
    file_calls = [call for call in fake.calls if kind(call) == "file"]
    assert plan["file"].calls == len(file_calls) > 3  # the oversized file is chunked plus combined
    assert plan["file"].exact and whole.chunked_files == [OVERSIZED_FILE]
    # Module/project counts are upper bounds: never below what a real run does.
    assert plan["module"].calls >= sum(1 for call in fake.calls if kind(call) == "module")
    assert plan["project"].calls >= sum(1 for call in fake.calls if kind(call) == "project")
    assert not plan["module"].exact and not plan["project"].exact


def test_file_pass_tokens_are_exact_for_unchunked_files(tmp_path: Path) -> None:
    make_project(tmp_path, PROJECT)
    config = Config()
    plan = passes(plan_for(tmp_path, config))
    fake = real_run(tmp_path, config)
    file_prompts = [call.prompt for call in fake.calls if kind(call) == "file"]
    assert plan["file"].calls == len(file_prompts) == 3  # the comment-only file needs no call
    assert plan["file"].tokens == sum(estimate_tokens(prompt) for prompt in file_prompts)
    module_prompts = [call.prompt for call in fake.calls if kind(call) == "module"]
    assert plan["module"].tokens >= sum(estimate_tokens(prompt) for prompt in module_prompts)


def test_hits_and_misses_against_a_populated_cache(tmp_path: Path, no_provider) -> None:
    make_project(tmp_path, PROJECT)
    config = Config()
    real_run(tmp_path, config)
    assert passes(plan_for(tmp_path, config))["file"].hits == 4
    (tmp_path / "pkg" / "b.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
    plan = passes(plan_for(tmp_path, config))
    assert (plan["file"].hits, plan["file"].misses, plan["file"].calls) == (3, 1, 1)
    assert plan["module"].hits is None and plan["module"].calls == 2
    result = run_cli(["scan", str(tmp_path), "--dry-run"])
    row = next(line for line in result.stdout.splitlines() if line.startswith("file "))
    assert row.split()[:5] == ["file", "4", "3", "1", "1"] and row.endswith("exact")
    assert next(line for line in result.stdout.splitlines() if line.startswith("module ")).endswith("upper bound")
    assert "upper bounds" in result.stdout


def test_peek_only_cache_is_left_byte_identical(tmp_path: Path, no_provider) -> None:
    make_project(tmp_path, PROJECT)
    real_run(tmp_path, Config())
    before = snapshot_tree(tmp_path)
    assert run_cli(["scan", str(tmp_path), "--dry-run"]).exit_code == 0
    assert snapshot_tree(tmp_path) == before


def test_skipped_files_are_listed_with_reasons(tmp_path: Path, no_provider) -> None:
    root = build_broken_files(tmp_path)
    (root / "codewhy.toml").write_text("[extraction]\nmax_file_bytes = 64\n", encoding="utf-8")
    result = run_cli(["scan", str(root), "--dry-run"])
    assert result.exit_code == 0, result.stderr
    assert "files discovered: 6 (3 skipped)" in result.stdout
    for line in (
        "skipped invalid_utf8.py (undecodable)",
        "skipped oversized.py (too_large)",
        "skipped syntax_error.py (SyntaxError: invalid syntax (line 1))",
    ):
        assert line in result.stdout


def test_plain_text_when_not_a_tty(tmp_path: Path, no_provider) -> None:
    make_project(tmp_path, PROJECT)
    result = run_cli(["scan", str(tmp_path), "--dry-run"])
    assert "\x1b" not in result.stdout
    assert "pass" in result.stdout.splitlines()[4] and "est. input tokens" in result.stdout


def test_no_supported_files_is_reported_the_same_way(tmp_path: Path, no_provider) -> None:
    make_project(tmp_path, {"README.md": "# nothing to scan\n"})
    result = run_cli(["scan", str(tmp_path), "--dry-run"])
    assert result.exit_code == 1
    assert "No supported source files found (supported: .py .js .jsx .mjs .cjs .ts .tsx .mts .cts)" in result.stderr
