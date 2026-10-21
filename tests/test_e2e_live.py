from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from tests.helpers import run_cli


@pytest.mark.live_llm
def test_live_scan_flat_scripts(tmp_path: Path) -> None:
    """Run the real configured provider; excluded from the default offline suite."""
    fixture = tmp_path / "flat_scripts"
    shutil.copytree(Path(__file__).parent / "fixtures" / "flat_scripts", fixture)
    result = run_cli(["scan", str(fixture)])
    assert result.exit_code == 0, result.stderr
    output = fixture / "PROJECT_DOCS.md"
    assert output.is_file()
    text = output.read_text(encoding="utf-8")
    assert "generated_by: codewhy" in text
    assert "#### Purpose" in text
    assert "[generation failed]" not in text
