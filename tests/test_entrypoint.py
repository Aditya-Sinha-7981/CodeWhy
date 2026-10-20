from __future__ import annotations

from pathlib import Path

import pytest

from codewhy.config import ConfigError, resolve_config
from codewhy.entrypoint import detect_entry_point
from codewhy.project import build_project
from tests.helpers import make_project


def detect(root: Path, config_overrides: dict[str, object] | None = None) -> str | None:
    config = resolve_config(root, config_overrides)
    return detect_entry_point(build_project(root, config), config)


def test_explicit_entry_point_wins(tmp_path: Path) -> None:
    make_project(tmp_path, {"main.py": "", "other.py": ""})

    assert detect(tmp_path, {"project.entry_point": "other.py"}) == "other.py"


def test_missing_explicit_entry_point_is_fatal(tmp_path: Path) -> None:
    make_project(tmp_path, {"main.py": ""})

    with pytest.raises(ConfigError, match="project.entry_point 'missing.py' does not exist in the scanned project"):
        detect(tmp_path, {"project.entry_point": "missing.py"})


def test_pyproject_scripts_resolve_package_from_src(tmp_path: Path) -> None:
    make_project(
        tmp_path,
        {
            "pyproject.toml": '[project.scripts]\nrun = "app.cli:main"\n',
            "src/app/cli.py": "def main():\n    pass\n",
            "main.py": "",
        },
    )

    assert detect(tmp_path) == "src/app/cli.py"


def test_unresolvable_script_falls_through_to_next_script(tmp_path: Path) -> None:
    make_project(
        tmp_path,
        {
            "pyproject.toml": (
                "[project.scripts]\n"
                'missing = "missing.cli:main"\n'
                'run = "app.cli:main"\n'
            ),
            "src/app/cli.py": "def main():\n    pass\n",
        },
    )

    assert detect(tmp_path) == "src/app/cli.py"


def test_poetry_scripts_follow_project_scripts(tmp_path: Path) -> None:
    make_project(
        tmp_path,
        {
            "pyproject.toml": '[tool.poetry.scripts]\nrun = "app.cli:main"\n',
            "src/app/cli.py": "def main():\n    pass\n",
        },
    )

    assert detect(tmp_path) == "src/app/cli.py"


def test_project_scripts_precede_poetry_scripts(tmp_path: Path) -> None:
    make_project(
        tmp_path,
        {
            "pyproject.toml": (
                '[project.scripts]\nproject = "app.project:main"\n'
                '[tool.poetry.scripts]\npoetry = "app.poetry:main"\n'
            ),
            "src/app/project.py": "",
            "src/app/poetry.py": "",
        },
    )

    assert detect(tmp_path) == "src/app/project.py"


def test_package_json_main_then_start(tmp_path: Path) -> None:
    make_project(tmp_path, {"package.json": '{"main":"missing.js","scripts":{"start":"node app.js"}}', "app.ts": ""})

    assert detect(tmp_path) == "app.ts"


@pytest.mark.parametrize("command", ["node --watch app.js", "ts-node -T app.js", "tsx app.js", "nodemon --quiet app.js"])
def test_package_json_start_accepts_supported_commands_and_flags(tmp_path: Path, command: str) -> None:
    make_project(tmp_path, {"package.json": f'{{"scripts":{{"start":"{command}"}}}}', "app.js": ""})

    assert detect(tmp_path) == "app.js"


@pytest.mark.parametrize("command", ["react-scripts start", "npm run x"])
def test_package_json_start_rejects_unsupported_commands(tmp_path: Path, command: str) -> None:
    make_project(tmp_path, {"package.json": f'{{"scripts":{{"start":"{command}"}}}}', "main.py": ""})

    assert detect(tmp_path) == "main.py"


@pytest.mark.parametrize("metadata", ["pyproject.toml", "package.json"])
def test_invalid_metadata_warns_and_falls_through(tmp_path: Path, metadata: str, caplog: pytest.LogCaptureFixture) -> None:
    content = "[project\n" if metadata == "pyproject.toml" else "{"
    make_project(tmp_path, {metadata: content, "main.py": ""})

    assert detect(tmp_path) == "main.py"
    assert metadata in caplog.text


@pytest.mark.parametrize("name", ["main.py", "__main__.py", "app.py", "index.js", "index.ts", "src/main.py", "src/index.js", "src/index.ts"])
def test_conventional_entry_point_order(tmp_path: Path, name: str) -> None:
    make_project(tmp_path, {name: ""})

    assert detect(tmp_path) == name


def test_no_entry_point_returns_none(tmp_path: Path) -> None:
    make_project(tmp_path, {"module.py": ""})

    assert detect(tmp_path) is None
