"""Pure Python import resolution against discovered project paths."""

from __future__ import annotations

from dataclasses import dataclass
import posixpath
from typing import Literal

from codewhy.extraction.model import ImportInfo


@dataclass(frozen=True)
class Resolved:
    targets: tuple[str, ...]


@dataclass(frozen=True)
class Unresolved:
    kind: Literal["external", "relative"]
    name: str


Resolution = Resolved | Unresolved


def _module_path(module: str) -> str:
    return module.replace(".", "/").strip("/")


def _candidates(path: str) -> tuple[str, ...]:
    return (f"{path}.py", f"{path}/__init__.py")


def _match_longest(root: str, dotted: str, project_paths: frozenset[str]) -> str | None:
    parts = [part for part in dotted.split(".") if part]
    for length in range(len(parts), 0, -1):
        relative = "/".join(parts[:length])
        candidate_base = posixpath.join(root, relative) if root else relative
        for candidate in _candidates(candidate_base):
            if candidate in project_paths:
                return candidate
    return None


def _rooted_candidates(root: str, relative: str, project_paths: frozenset[str]) -> str | None:
    candidate_base = posixpath.join(root, relative) if root else relative
    for candidate in _candidates(candidate_base):
        if candidate in project_paths:
            return candidate
    return None


def _relative_base(importer_path: str, level: int) -> str | None:
    base = posixpath.dirname(importer_path)
    for _ in range(max(level - 1, 0)):
        parent = posixpath.dirname(base)
        if parent == base:
            return None
        base = parent
    return base


def _relative_name(imp: ImportInfo) -> str:
    level = imp.level if type(imp.level) is int and imp.level >= 1 else 0
    module = imp.module if isinstance(imp.module, str) else ""
    return "." * level + module


def _resolve_relative(
    importer_path: str,
    imp: ImportInfo,
    project_paths: frozenset[str],
) -> Resolution:
    base = _relative_base(importer_path, imp.level)
    if base is None:
        return Unresolved("relative", _relative_name(imp))

    if imp.module:
        # `from .a import b`: per name prefer the submodule `a/b.py`, else the
        # file for `a` itself (the same rule as absolute `from a import b`).
        module_base = _module_path(imp.module)
        package = _rooted_candidates(base, module_base, project_paths)
        targets = set()
        for name in imp.names:
            child = _rooted_candidates(base, f"{module_base}/{name}", project_paths) if name != "*" else None
            if child or package:
                targets.add(child or package)
        if not targets and package:
            targets.add(package)
        if targets:
            return Resolved(tuple(sorted(targets)))
        return Unresolved("relative", _relative_name(imp))

    targets: set[str] = set()
    missing = False
    for name in imp.names:
        if name == "*":
            missing = True
            continue
        target = _rooted_candidates(base, name, project_paths)
        if target:
            targets.add(target)
        else:
            missing = True
    if missing:
        fallback = _rooted_candidates(base, "__init__", project_paths)
        if fallback:
            targets.add(fallback)
    if targets:
        return Resolved(tuple(sorted(targets)))
    return Unresolved("relative", _relative_name(imp))


def _absolute_roots(importer_path: str) -> tuple[str, ...]:
    importer_dir = posixpath.dirname(importer_path)
    return tuple(dict.fromkeys((importer_dir, "", "src")))


def _resolve_absolute(
    importer_path: str,
    imp: ImportInfo,
    project_paths: frozenset[str],
) -> Resolution:
    module = imp.module
    if not module:
        return Unresolved("external", "")

    for root in _absolute_roots(importer_path):
        if imp.names:
            targets: set[str] = set()
            base_target = _match_longest(root, module, project_paths)
            for name in imp.names:
                if name == "*":
                    continue
                child = _match_longest(root, f"{module}.{name}", project_paths)
                if child or base_target:
                    targets.add(child or base_target)
            if not targets and base_target:
                # Only `*` was imported: the module file itself is the target.
                targets.add(base_target)
            if targets:
                return Resolved(tuple(sorted(targets)))
        else:
            target = _match_longest(root, module, project_paths)
            if target:
                return Resolved((target,))
    return Unresolved("external", module.split(".", 1)[0])


def resolve_python_import(
    importer_path: str,
    imp: ImportInfo,
    project_paths: frozenset[str],
) -> Resolution:
    """Resolve one Python import using only the supplied project paths."""
    level = imp.level if type(imp.level) is int else None
    module = imp.module if isinstance(imp.module, str) else ""
    if level is None:
        return Unresolved("external", module.split(".", 1)[0])
    try:
        if level >= 1:
            return _resolve_relative(importer_path, imp, project_paths)
        return _resolve_absolute(importer_path, imp, project_paths)
    except (AttributeError, TypeError, ValueError):
        name = _relative_name(imp) if level >= 1 else module.split(".", 1)[0]
        return Unresolved("relative" if level >= 1 else "external", name)
