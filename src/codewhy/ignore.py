"""File discovery: which files under the project root are in play.

Rules (docs/ARCHITECTURE.md -> Config, ignore, and discovery):

* Built-in ignore patterns come first, then `.toolignore`, in one ordered
  gitignore-style spec, so a later `!build/` can re-include a built-in.
* `.gitignore` is never read.
* codewhy's own artifacts are always excluded and cannot be re-included.
* Ignored directories are pruned (never listed); symlinks are never followed.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pathspec import GitIgnoreSpec

from codewhy.config import CONFIG_FILE_NAME, Config

logger = logging.getLogger(__name__)

TOOLIGNORE_FILE_NAME = ".toolignore"
CACHE_DIR_NAME = ".codewhy"

BUILTIN_IGNORE_PATTERNS: tuple[str, ...] = (
    "node_modules/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".git/",
    "dist/",
    "build/",
    ".next/",
    "*.min.js",
    "*.d.ts",
    ".pytest_cache/",
    ".mypy_cache/",
    ".idea/",
    ".vscode/",
)

LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
}

_UTF8_BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True)
class SourceFile:
    path: str
    language: str
    text: str
    size: int


@dataclass(frozen=True)
class Skip:
    path: str
    reason: Literal["too_large", "undecodable", "unreadable"]
    detail: str


@dataclass(frozen=True)
class DiscoveryResult:
    files: list[SourceFile]
    skips: list[Skip]
    other_files: list[str]


class DiscoveryError(Exception):
    """The project root itself is unusable (missing or not a directory)."""


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def language_for(path: str, languages: list[str]) -> str | None:
    """Language for a supported, enabled extension; None otherwise."""
    _, ext = os.path.splitext(path)
    language = LANGUAGE_BY_EXTENSION.get(ext)
    return language if language in languages else None


def _read_toolignore(root: Path) -> list[str]:
    path = root / TOOLIGNORE_FILE_NAME
    if not path.is_file() or path.is_symlink():
        return []
    try:
        data = path.read_bytes()
    except OSError as exc:
        logger.warning("cannot read %s (%s); using built-in ignores only", TOOLIGNORE_FILE_NAME, exc.strerror or exc)
        return []
    if data.startswith(_UTF8_BOM):
        data = data[len(_UTF8_BOM) :]
    return normalize_newlines(data.decode("utf-8", errors="replace")).split("\n")


def build_ignore_spec(root: Path) -> GitIgnoreSpec:
    """Built-in patterns followed by valid `.toolignore` lines, in that order."""
    lines: list[str] = list(BUILTIN_IGNORE_PATTERNS)
    for number, line in enumerate(_read_toolignore(root), start=1):
        try:
            GitIgnoreSpec.from_lines([line])
        except Exception as exc:  # pathspec raises its own error types per version
            logger.warning("%s line %d: ignoring invalid pattern %r (%s)", TOOLIGNORE_FILE_NAME, number, line, exc)
            continue
        lines.append(line)
    return GitIgnoreSpec.from_lines(lines)


def _always_excluded(config: Config) -> tuple[frozenset[str], frozenset[str]]:
    """(excluded directory paths, excluded file paths), all project-relative."""
    dirs = frozenset({config.output.dir})
    files = frozenset({config.output.single_file_name, CONFIG_FILE_NAME, TOOLIGNORE_FILE_NAME})
    return dirs, files


def _is_link(entry: os.DirEntry[str]) -> bool:
    if entry.is_symlink():
        return True
    is_junction = getattr(entry, "is_junction", None)  # Windows junctions, Python 3.12+
    return bool(is_junction and is_junction())


def _read_source(path: Path, rel: str, language: str, size: int, max_bytes: int) -> SourceFile | Skip:
    if size > max_bytes:
        return Skip(rel, "too_large", f"{size} bytes exceeds max_file_bytes ({max_bytes})")
    try:
        data = path.read_bytes()
    except OSError as exc:
        return Skip(rel, "unreadable", exc.strerror or type(exc).__name__)
    if data.startswith(_UTF8_BOM):
        data = data[len(_UTF8_BOM) :]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return Skip(rel, "undecodable", f"invalid UTF-8 at byte {exc.start}")
    return SourceFile(path=rel, language=language, text=normalize_newlines(text), size=size)


def discover(root: Path, config: Config) -> DiscoveryResult:
    """Walk `root` and classify every non-ignored file. Deterministic for a given tree."""
    if not root.exists():
        raise DiscoveryError(f"path does not exist: {root}")
    if not root.is_dir():
        raise DiscoveryError(f"path is not a directory: {root}")

    spec = build_ignore_spec(root)
    excluded_dirs, excluded_files = _always_excluded(config)
    languages = list(config.extraction.languages)
    max_bytes = config.extraction.max_file_bytes

    files: list[SourceFile] = []
    skips: list[Skip] = []
    other: list[str] = []

    # Explicit stack instead of os.walk so pruning and symlink handling are
    # visible; entries are sorted by name so the walk order is stable.
    stack: list[tuple[Path, str]] = [(root, "")]
    while stack:
        directory, prefix = stack.pop()
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            logger.warning("cannot list directory %s (%s); skipping it", prefix or ".", exc.strerror or exc)
            continue
        subdirs: list[tuple[Path, str]] = []
        for entry in entries:
            rel = f"{prefix}{entry.name}"
            if _is_link(entry):
                logger.debug("not following symlink %s", rel)
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                is_dir = False
            if is_dir:
                if entry.name == CACHE_DIR_NAME or rel in excluded_dirs:
                    continue
                # A directory pattern like `build/` only matches with the
                # trailing slash. Pruning here is also why `!build/keep.py`
                # cannot re-include a file under an ignored `build/` (same as
                # git): the walker never looks inside the directory.
                if spec.match_file(rel + "/"):
                    continue
                subdirs.append((Path(entry.path), rel + "/"))
                continue
            if rel in excluded_files or spec.match_file(rel):
                continue
            language = language_for(rel, languages)
            if language is None:
                other.append(rel)
                continue
            try:
                size = entry.stat(follow_symlinks=False).st_size
            except OSError as exc:
                skips.append(Skip(rel, "unreadable", exc.strerror or type(exc).__name__))
                continue
            result = _read_source(Path(entry.path), rel, language, size, max_bytes)
            if isinstance(result, Skip):
                logger.warning("skipping %s: %s", rel, result.detail)
                skips.append(result)
            else:
                files.append(result)
        stack.extend(reversed(subdirs))

    files.sort(key=lambda f: f.path)
    skips.sort(key=lambda s: s.path)
    other.sort()
    return DiscoveryResult(files=files, skips=skips, other_files=other)
