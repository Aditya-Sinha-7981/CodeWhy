from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from codewhy.config import Config
from codewhy.llm.budget import estimate_tokens
from tests.helpers import run_cli

FIXTURE_FILES = ["main.py", "solver.py", "utils.py"]


@pytest.mark.live_llm
def test_live_scan_flat_scripts(tmp_path: Path) -> None:
    """Run the real configured provider; excluded from the default offline suite. Asserts structure, not wording."""
    fixture = tmp_path / "flat_scripts"
    shutil.copytree(Path(__file__).parent / "fixtures" / "flat_scripts", fixture)
    result = run_cli(["scan", str(fixture), "--yes"])
    assert result.exit_code == 0, result.stderr
    text = (fixture / "PROJECT_DOCS.md").read_text(encoding="utf-8")

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
