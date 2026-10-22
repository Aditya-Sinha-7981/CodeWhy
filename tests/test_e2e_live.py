from __future__ import annotations

import os
from pathlib import Path
import re
import shutil

import pytest

from codewhy.config import Config, resolve_config
from codewhy.llm.budget import estimate_tokens
from codewhy.project import build_project
from tests.fixtures.builders import build_broken_files, build_oversized_file
from tests.helpers import run_cli

FIXTURE_FILES = ["main.py", "solver.py", "utils.py"]
FIXTURES = Path(__file__).parent / "fixtures"
# Optional: run against another provider/model than the configured default, e.g.
# CODEWHY_LIVE_MODEL=qwen2.5-coder:7b .venv/bin/python -m pytest -q -m live_llm
# CODEWHY_LIVE_PROVIDER=gemini CODEWHY_LIVE_MODEL=<id> ... (needs GEMINI_API_KEY)
LIVE_PROVIDER = os.environ.get("CODEWHY_LIVE_PROVIDER")
LIVE_MODEL = os.environ.get("CODEWHY_LIVE_MODEL")
OVERRIDES = [*(["--provider", LIVE_PROVIDER] if LIVE_PROVIDER else []), *(["--model", LIVE_MODEL] if LIVE_MODEL else [])]


@pytest.mark.live_llm
def test_live_scan_flat_scripts(tmp_path: Path) -> None:
    """Run the real configured provider; excluded from the default offline suite. Asserts structure, not wording."""
    fixture = tmp_path / "flat_scripts"
    shutil.copytree(Path(__file__).parent / "fixtures" / "flat_scripts", fixture)
    args = ["scan", str(fixture), "--yes", *OVERRIDES]
    result = run_cli(args)
    assert result.exit_code == 0, result.stderr
    first = (fixture / "PROJECT_DOCS.md").read_bytes()
    text = first.decode("utf-8")

    block = text.split("\n---\n", 1)[0].removeprefix("---\n")
    meta = dict(line.split(": ", 1) for line in block.split("\n"))
    assert meta["generated_by"].startswith("codewhy v") and meta["source"] == "project"
    assert meta["generated_at"].endswith("Z")

    assert "(level 0)" in result.stdout
    assert estimate_tokens(text) <= Config().output.token_budget

    order = [text.index(h) for h in ("## Overview", "### Architecture", "### Entry Point", "## Modules", "## Files")]
    assert order == sorted(order)
    assert "[generation failed]" not in text
    for path in FIXTURE_FILES:
        purpose = text.split(f"### `{path}`\n\n#### Purpose\n\n", 1)[1].split("\n\n", 1)[0]
        assert purpose.strip() and purpose != "[generation failed]"

    # Re-running immediately is served entirely from the cache: no generation, same bytes.
    again = run_cli(args)
    assert again.exit_code == 0, again.stderr
    assert " 0 generated, 0 failed" in again.stdout and "(generated)" not in again.stdout
    assert (fixture / "PROJECT_DOCS.md").read_bytes() == first


ITEM = re.compile(r"^\[\d+/\d+\] (\w+)  (.*)  \((\w+)\)$")


def _copy(name: str):
    def build(tmp_path: Path) -> Path:
        target = tmp_path / name
        shutil.copytree(FIXTURES / name, target, ignore=shutil.ignore_patterns("__pycache__"))
        return target

    return build


def _oversized(tmp_path: Path) -> Path:
    # About 28k estimated tokens: over the prompt budget of every default
    # provider configuration, so the file is chunked and combined.
    return build_oversized_file(tmp_path, chunk_tokens=4000)


def _broken(tmp_path: Path) -> Path:
    root = build_broken_files(tmp_path)
    (root / "codewhy.toml").write_text("[extraction]\nmax_file_bytes = 64\n", encoding="utf-8")
    return root


E2E = {
    "flat_scripts": (_copy("flat_scripts"), 0, []),
    "nested_modules": (_copy("nested_modules"), 0, []),
    "python_js_mixed": (_copy("python_js_mixed"), 0, []),
    "oversized_file": (_oversized, 0, []),
    "broken_files": (
        _broken,
        2,
        [
            "skipped invalid_utf8.py (undecodable)",
            "skipped oversized.py (too_large)",
            "skipped syntax_error.py (SyntaxError: invalid syntax (line 1))",
        ],
    ),
}


def _items(stdout: str) -> dict[tuple[str, str], str]:
    return {(m[1], m[2]): m[3] for line in stdout.splitlines() if (m := ITEM.match(line))}


@pytest.mark.live_llm
@pytest.mark.parametrize("name", sorted(E2E))
def test_live_e2e_fixture(name: str, tmp_path: Path) -> None:
    """docs/TESTS.md -> E2E. Structure, not wording; the immediate re-run must be free and identical."""
    build, expected_exit, skip_lines = E2E[name]
    root = build(tmp_path)
    args = ["scan", str(root), "--yes", *OVERRIDES]
    first = run_cli(args)
    assert first.exit_code == expected_exit, (first.stdout, first.stderr)
    for line in skip_lines:
        assert line in first.stdout
    text = (root / "PROJECT_DOCS.md").read_text(encoding="utf-8")
    block = text.split("\n---\n", 1)[0].removeprefix("---\n")
    meta = dict(line.split(": ", 1) for line in block.split("\n"))
    assert meta["generated_by"].startswith("codewhy v") and meta["generated_at"].endswith("Z")
    assert "[generation failed]" not in text
    project = build_project(root, resolve_config(root))
    for path, fs in project.structures.items():
        if fs.status == "ok":
            assert f"### `{path}`" in text or f"- `{path}` — " in text, path
    assert " 0 failed" in first.stdout

    again = run_cli(args)
    assert again.exit_code == expected_exit
    assert " 0 generated, 0 failed" in again.stdout, again.stdout
    assert (root / "PROJECT_DOCS.md").read_text(encoding="utf-8") == text


@pytest.mark.live_llm
def test_live_edit_one_file_reruns_only_what_the_cache_rules_allow(tmp_path: Path) -> None:
    root = _copy("nested_modules")(tmp_path)
    args = ["scan", str(root), "--yes", *OVERRIDES]
    assert run_cli(args).exit_code == 0
    edited = "src/app/utils.py"
    target = root / edited
    target.write_text(target.read_text(encoding="utf-8") + "\n\nRETRY_LIMIT = 3\n", encoding="utf-8")
    result = run_cli(args)
    assert result.exit_code == 0, result.stderr
    items = _items(result.stdout)
    assert items[("file", edited)] == "generated"
    # No import changed, so every other file's prompt inputs are identical: all cached.
    other_files = {subject: status for (kind, subject), status in items.items() if kind == "file" and subject != edited}
    assert set(other_files.values()) == {"cached"}, other_files
    # Modules other than the edited file's own folder cannot re-run.
    other_modules = {subject: status for (kind, subject), status in items.items() if kind == "module" and subject != "src/app"}
    assert set(other_modules.values()) == {"cached"}, other_modules
