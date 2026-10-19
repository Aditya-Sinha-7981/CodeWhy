"""ProjectModel: discovered files grouped into modules (docs/ARCHITECTURE.md -> Module Definition)."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path

from codewhy.config import Config
from codewhy.ignore import Skip, SourceFile, discover

ROOT_MODULE = "."


@dataclass(frozen=True)
class Module:
    path: str  # "." for the project root
    files: list[str]  # sorted, direct members only


@dataclass(frozen=True)
class ProjectModel:
    root: Path
    name: str
    files: dict[str, SourceFile]
    modules: list[Module]
    other_files: list[str]
    skips: list[Skip]


def module_path_of(file_path: str) -> str:
    return posixpath.dirname(file_path) or ROOT_MODULE


def group_modules(paths: list[str]) -> list[Module]:
    """One module per folder that directly contains a file; root first, then by path."""
    grouped: dict[str, list[str]] = {}
    for path in paths:
        grouped.setdefault(module_path_of(path), []).append(path)
    order = sorted(grouped, key=lambda m: (m != ROOT_MODULE, m))
    return [Module(path=m, files=sorted(grouped[m])) for m in order]


def build_project(root: Path, config: Config) -> ProjectModel:
    resolved = root.resolve()
    result = discover(resolved, config)
    files = {f.path: f for f in result.files}
    return ProjectModel(
        root=resolved,
        name=resolved.name,
        files=files,
        modules=group_modules(list(files)),
        other_files=result.other_files,
        skips=result.skips,
    )
