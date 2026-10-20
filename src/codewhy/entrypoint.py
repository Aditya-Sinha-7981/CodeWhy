"""Deterministic entry-point detection."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import tomllib
from typing import TYPE_CHECKING

from codewhy.config import Config, ConfigError
from codewhy.extraction.model import ImportInfo
from codewhy.graph.js_resolver import Resolved as JsResolved
from codewhy.graph.js_resolver import resolve_js_import
from codewhy.graph.python_resolver import Resolved as PythonResolved
from codewhy.graph.python_resolver import resolve_python_import

if TYPE_CHECKING:
    from codewhy.project import ProjectModel


logger = logging.getLogger(__name__)
START_COMMAND = re.compile(r"^(node|ts-node|tsx|nodemon)\s+(?:-\S+\s+)*(\S+)\s*$")
CONVENTIONAL = ("main.py", "__main__.py", "app.py", "index.js", "index.ts", "src/main.py", "src/index.js", "src/index.ts")


def _supported(project: ProjectModel) -> frozenset[str]:
    return frozenset(path for path, structure in project.structures.items() if structure.status == "ok")


def _python_script_target(value: object, project_paths: frozenset[str]) -> str | None:
    if not isinstance(value, str) or ":" not in value:
        return None
    module, _function = value.split(":", 1)
    if not module:
        return None
    result = resolve_python_import("", ImportInfo("python", module, (), 0, 1, False), project_paths)
    if isinstance(result, PythonResolved) and result.targets:
        return result.targets[0]
    return None


def _js_path_target(value: object, project_paths: frozenset[str]) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    specifier = value if value.startswith((".", "/")) else f"./{value}"
    result = resolve_js_import("package.json", ImportInfo("js", specifier, (), 0, 1, False), project_paths)
    if isinstance(result, JsResolved) and result.targets:
        return result.targets[0]
    return None


def _read_metadata(path: Path) -> str | None:
    """Text of a root metadata file; None (with a warning) if absent or unreadable."""
    if not path.is_file():
        return None
    try:
        # utf-8-sig: editors on Windows often write a BOM into these files.
        return path.read_bytes().decode("utf-8-sig")
    except OSError as exc:
        logger.warning("cannot read project metadata %s: %s", path.name, exc.strerror or exc)
    except UnicodeDecodeError as exc:
        logger.warning("cannot read project metadata %s: invalid UTF-8 at byte %d", path.name, exc.start)
    return None


def _table(data: object, *keys: str) -> dict[str, object]:
    """Nested table at `keys`, or {} when any level is missing or not a table."""
    for key in keys:
        if not isinstance(data, dict):
            return {}
        data = data.get(key)
    return data if isinstance(data, dict) else {}


def _toml_target(root: Path, project_paths: frozenset[str]) -> str | None:
    text = _read_metadata(root / "pyproject.toml")
    if text is None:
        return None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        logger.warning("cannot read project metadata pyproject.toml: %s", exc)
        return None
    for section in (_table(data, "project", "scripts"), _table(data, "tool", "poetry", "scripts")):
        for value in section.values():
            target = _python_script_target(value, project_paths)
            if target:
                return target
    return None


def _package_target(root: Path, project_paths: frozenset[str]) -> str | None:
    text = _read_metadata(root / "package.json")
    if text is None:
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        logger.warning("cannot read project metadata package.json: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    target = _js_path_target(data.get("main"), project_paths)
    if target:
        return target
    scripts = data.get("scripts")
    start = scripts.get("start") if isinstance(scripts, dict) else None
    if isinstance(start, str):
        match = START_COMMAND.fullmatch(start)
        if match:
            return _js_path_target(match.group(2), project_paths)
    return None


def _skip_reason(project: ProjectModel, path: str) -> str | None:
    """Why a supported file was left out (discovery skip or failed extraction), if it was."""
    for skip in project.skips:
        if skip.path == path:
            return skip.reason
    structure = project.structures.get(path)
    if structure is not None and structure.status != "ok":
        return structure.warning or "extraction failed"
    return None


def detect_entry_point(project: ProjectModel, config: Config) -> str | None:
    """Return the first deterministic entry point for a project."""
    explicit = config.project.entry_point
    supported = _supported(project)
    if explicit is not None:
        if explicit in supported:
            return explicit
        skip = _skip_reason(project, explicit)
        if skip is not None:
            raise ConfigError([f"project.entry_point '{explicit}' is in the scanned project but was skipped ({skip})"])
        raise ConfigError([f"project.entry_point '{explicit}' does not exist in the scanned project"])

    python_paths = frozenset(path for path in supported if project.structures[path].language == "python")
    target = _toml_target(project.root, python_paths)
    if target:
        return target

    js_paths = frozenset(
        path for path in supported if project.structures[path].language in {"javascript", "typescript"}
    )
    target = _package_target(project.root, js_paths)
    if target:
        return target

    return next((path for path in CONVENTIONAL if path in supported), None)
