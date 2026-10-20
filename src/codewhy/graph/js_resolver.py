"""Pure JavaScript and TypeScript import resolution."""

from __future__ import annotations

from dataclasses import dataclass
import posixpath
from typing import Literal

from codewhy.extraction.model import ImportInfo


SUPPORTED_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
TS_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts")
JS_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs")


@dataclass(frozen=True)
class Resolved:
    targets: tuple[str, ...]


@dataclass(frozen=True)
class Unresolved:
    kind: Literal["external", "relative"]
    name: str


Resolution = Resolved | Unresolved


def _package_name(specifier: str) -> str:
    parts = specifier.split("/")
    if specifier.startswith("@") and parts[0] != "@" and len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0]


def _escapes_root(path: str) -> bool:
    return path == ".." or path.startswith("../")


def _candidate(base: str, project_paths: frozenset[str]) -> str | None:
    if base in project_paths and posixpath.splitext(base)[1] in SUPPORTED_EXTENSIONS:
        return base
    for extension in SUPPORTED_EXTENSIONS:
        target = f"{base}{extension}"
        if target in project_paths:
            return target
    for extension in SUPPORTED_EXTENSIONS:
        target = posixpath.join(base, f"index{extension}")
        if target in project_paths:
            return target
    return None


def _ts_esm_candidate(base: str, project_paths: frozenset[str]) -> str | None:
    stem, extension = posixpath.splitext(base)
    if extension not in JS_EXTENSIONS:
        return None
    for ts_extension in TS_EXTENSIONS:
        target = f"{stem}{ts_extension}"
        if target in project_paths:
            return target
    return None


def resolve_js_import(
    importer_path: str,
    imp: ImportInfo,
    project_paths: frozenset[str],
) -> Resolution:
    """Resolve one JS/TS import using only the supplied project paths."""
    specifier = imp.module if isinstance(imp.module, str) else ""
    if not specifier.startswith((".", "/")):
        return Unresolved("external", _package_name(specifier))

    try:
        if specifier.startswith("/"):
            base = posixpath.normpath(specifier.lstrip("/"))
        else:
            base = posixpath.normpath(posixpath.join(posixpath.dirname(importer_path), specifier))
        if _escapes_root(base):
            return Unresolved("relative", specifier)
        if base == ".":
            # normpath spells the project root "."; "" makes the root's index.* reachable.
            base = ""

        target = _candidate(base, project_paths)
        if target is None:
            target = _ts_esm_candidate(base, project_paths)
        return Resolved((target,)) if target else Unresolved("relative", specifier)
    except (AttributeError, TypeError, ValueError):
        return Unresolved("relative", specifier)
