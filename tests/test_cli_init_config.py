import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from codewhy import __version__
from codewhy.cli import template_bytes
from codewhy.config import Config, resolve_config
from tests.fixtures.builders import build_ignored_content
from tests.helpers import make_project, run_cli, snapshot_tree

REPO_ROOT = Path(__file__).resolve().parents[1]


# --- version ---------------------------------------------------------------


def test_version_flag() -> None:
    result = run_cli(["--version"])
    assert result.exit_code == 0
    assert result.stdout == "codewhy 0.1.0\n"
    assert __version__ == "0.1.0"


def test_python_dash_m_version() -> None:
    out = subprocess.run([sys.executable, "-m", "codewhy", "--version"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "codewhy 0.1.0"


# --- templates --------------------------------------------------------------


def test_example_is_byte_identical_to_packaged_template() -> None:
    assert (REPO_ROOT / "codewhy.toml.example").read_bytes() == template_bytes("codewhy.toml")


def test_config_template_loads_to_defaults(tmp_path: Path) -> None:
    make_project(tmp_path, {"codewhy.toml": template_bytes("codewhy.toml")})
    assert resolve_config(tmp_path).model_dump() == Config().model_dump()


def test_config_template_mentions_every_key() -> None:
    text = template_bytes("codewhy.toml").decode("utf-8")
    for section, model in Config.model_fields.items():
        assert f"[{section}]" in text
        for key in model.annotation.model_fields:  # type: ignore[union-attr]
            assert f"{key} =" in text, f"{section}.{key} missing from template"


def test_toolignore_template_documents_the_rules() -> None:
    text = template_bytes("toolignore").decode("utf-8")
    assert "# !build/" in text
    assert ".gitignore" in text and "never" in text
    assert "Built-in" in text
    # Every line is a comment or blank, so the template changes nothing by itself.
    assert all(not line or line.startswith("#") for line in text.splitlines())


# --- init -------------------------------------------------------------------


def test_init_creates_both_files(tmp_path: Path) -> None:
    result = run_cli(["init", str(tmp_path)])
    assert result.exit_code == 0
    assert result.stdout == "created codewhy.toml\ncreated .toolignore\n"
    assert (tmp_path / "codewhy.toml").read_bytes() == template_bytes("codewhy.toml")
    assert (tmp_path / ".toolignore").read_bytes() == template_bytes("toolignore")


def test_init_defaults_to_current_directory(tmp_path: Path) -> None:
    assert run_cli(["init"], cwd=tmp_path).exit_code == 0
    assert (tmp_path / "codewhy.toml").is_file()


def test_init_twice_skips_and_never_clobbers(tmp_path: Path) -> None:
    run_cli(["init", str(tmp_path)])
    (tmp_path / "codewhy.toml").write_text("[llm]\nseed = 42\n")
    before = snapshot_tree(tmp_path)
    result = run_cli(["init", str(tmp_path)])
    assert result.exit_code == 0
    assert result.stdout == "skipped codewhy.toml (already exists)\nskipped .toolignore (already exists)\n"
    assert snapshot_tree(tmp_path) == before


def test_init_fills_in_only_the_missing_file(tmp_path: Path) -> None:
    make_project(tmp_path, {".toolignore": "mine/\n"})
    result = run_cli(["init", str(tmp_path)])
    assert result.stdout == "created codewhy.toml\nskipped .toolignore (already exists)\n"
    assert (tmp_path / ".toolignore").read_text() == "mine/\n"


def test_init_bad_path(tmp_path: Path) -> None:
    result = run_cli(["init", str(tmp_path / "missing")])
    assert result.exit_code == 1
    assert "does not exist" in result.stderr


# --- config show ------------------------------------------------------------


def parse_shown(stdout: str) -> dict:
    return tomllib.loads(stdout)


def test_config_show_defaults(tmp_path: Path) -> None:
    before = snapshot_tree(tmp_path)
    result = run_cli(["config", "show", str(tmp_path)])
    assert result.exit_code == 0
    assert "# source: defaults" in result.stdout
    assert "# effective model: qwen2.5-coder:14b" in result.stdout
    assert Config.model_validate(parse_shown(result.stdout)).model_dump() == Config().model_dump()
    assert snapshot_tree(tmp_path) == before


def test_config_show_reflects_file_and_overrides(tmp_path: Path) -> None:
    make_project(tmp_path, {"codewhy.toml": '[llm]\nmodel = "file-model"\nseed = 5\n[output]\ntoken_budget = 1234\n'})
    before = snapshot_tree(tmp_path)
    result = run_cli(["config", "show", str(tmp_path), "--provider", "gemini", "--model", "cli-model"])
    assert result.exit_code == 0, result.stderr
    assert f"# source: {tmp_path / 'codewhy.toml'}" in result.stdout
    shown = parse_shown(result.stdout)
    assert shown["llm"]["provider"] == "gemini"
    assert shown["llm"]["model"] == "cli-model"
    assert shown["llm"]["seed"] == 5
    assert shown["output"]["token_budget"] == 1234
    assert "# effective context_tokens: 32768" in result.stdout
    assert snapshot_tree(tmp_path) == before


def test_config_show_bad_config_exits_1(tmp_path: Path) -> None:
    make_project(tmp_path, {"codewhy.toml": '[llm]\nmodle = "x"\ntemperature = 9\n'})
    result = run_cli(["config", "show", str(tmp_path)])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert 'unknown key [llm].modle (did you mean "model"?)' in result.stderr
    assert "[llm].temperature" in result.stderr
    assert "Traceback" not in result.stderr


def test_config_show_invalid_toml_exits_1(tmp_path: Path) -> None:
    make_project(tmp_path, {"codewhy.toml": "[llm\n"})
    result = run_cli(["config", "show", str(tmp_path)])
    assert result.exit_code == 1
    assert "line 1" in result.stderr


def test_config_show_unknown_provider_exits_1(tmp_path: Path) -> None:
    result = run_cli(["config", "show", str(tmp_path), "--provider", "openai"])
    assert result.exit_code == 1
    assert "'ollama', 'openai-compat' or 'gemini'" in result.stderr


# --- scan stub ----------------------------------------------------------------


def test_scan_stub_reports_counts_and_writes_nothing(tmp_path: Path) -> None:
    root = build_ignored_content(tmp_path)
    make_project(root, {"bad.py": b"\xff\xfe", "huge.js": "x" * 200, "codewhy.toml": "[extraction]\nmax_file_bytes = 150\n"})
    before = snapshot_tree(root)
    result = run_cli(["scan", str(root)])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Found 5 source files in 5 modules with 1 import edges (2 skipped, 1 other files). "
        "The documentation pipeline is not implemented yet.",
        "skipped bad.py (undecodable)",
        "skipped huge.js (too_large)",
    ]
    assert snapshot_tree(root) == before


def test_scan_defaults_to_current_directory(tmp_path: Path) -> None:
    make_project(tmp_path, {"main.py": ""})
    result = run_cli(["scan"], cwd=tmp_path)
    assert result.exit_code == 0
    assert result.stdout.startswith("Found 1 source files in 1 modules with 0 import edges (0 skipped, 0 other files).")


@pytest.mark.parametrize("target", ["missing", "file.py"])
def test_scan_invalid_path_exits_1(tmp_path: Path, target: str) -> None:
    make_project(tmp_path, {"file.py": ""})
    result = run_cli(["scan", str(tmp_path / target)])
    assert result.exit_code == 1
    assert result.stderr.startswith("error: ")
    assert len(result.stderr.splitlines()) == 1


def test_scan_bad_config_exits_1(tmp_path: Path) -> None:
    make_project(tmp_path, {"main.py": "", "codewhy.toml": "[output]\ndir = '/abs'\n"})
    result = run_cli(["scan", str(tmp_path)])
    assert result.exit_code == 1
    assert "[output].dir" in result.stderr
