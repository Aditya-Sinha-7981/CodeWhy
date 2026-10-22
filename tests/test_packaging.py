"""Built-wheel contents (task 09 §8). Point CODEWHY_WHEEL at a wheel to run it:

    uv build && CODEWHY_WHEEL=dist/codewhy-0.1.0-py3-none-any.whl .venv/bin/python -m pytest -q tests/test_packaging.py

Skipped otherwise, because building a wheel needs the network (build isolation)
and the default suite is offline. CI's wheel job sets CODEWHY_WHEEL.
"""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path

import pytest

from codewhy import __version__

WHEEL = os.environ.get("CODEWHY_WHEEL")
pytestmark = pytest.mark.skipif(not WHEEL, reason="set CODEWHY_WHEEL to a built wheel")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def names() -> list[str]:
    with zipfile.ZipFile(WHEEL) as wheel:  # type: ignore[arg-type]
        return wheel.namelist()


def test_wheel_contains_the_package_and_its_templates(names: list[str]) -> None:
    assert "codewhy/templates/codewhy.toml" in names
    assert "codewhy/templates/toolignore" in names
    assert "codewhy/cli.py" in names and "codewhy/__main__.py" in names
    expected = sorted(
        path.relative_to(ROOT / "src").as_posix()
        for path in (ROOT / "src" / "codewhy").rglob("*.py")
        if "__pycache__" not in path.parts
    )
    assert sorted(name for name in names if name.startswith("codewhy/") and name.endswith(".py")) == expected


def test_wheel_excludes_tests_and_repository_material(names: list[str]) -> None:
    for name in names:
        assert not name.startswith(("tests/", "docs/", "logs/", "eval/", "examples/", "scripts/")), name
        assert "__pycache__" not in name and not name.endswith(".pyc"), name


def test_entry_point_version_and_license(names: list[str]) -> None:
    with zipfile.ZipFile(WHEEL) as wheel:  # type: ignore[arg-type]
        info = next(name for name in names if name.endswith(".dist-info/entry_points.txt"))
        assert "codewhy = codewhy.cli:app" in wheel.read(info).decode()
        metadata = wheel.read(next(name for name in names if name.endswith(".dist-info/METADATA"))).decode()
    assert f"\nVersion: {__version__}\n" in metadata
    assert "\nLicense-Expression: MIT\n" in metadata
    assert any(name.endswith(".dist-info/licenses/LICENSE") for name in names)


def test_changelog_names_the_package_version() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^## \[{re.escape(__version__)}\]", changelog, re.MULTILINE)
