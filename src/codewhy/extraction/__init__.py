"""Public extraction entry points."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING

from .model import FileStructure
from .python_ast import extract_python
from .ts_treesitter import extract_typescript

if TYPE_CHECKING:
    from codewhy.ignore import SourceFile


logger = logging.getLogger(__name__)


def _skipped(path: str, language: str, text: str, warning: str) -> FileStructure:
    return FileStructure(
        path=path,
        language=language,
        source_text=text,
        line_count=len(text.splitlines()),
        status="skipped",
        warning=warning,
    )


def extract_file(source_file: SourceFile) -> FileStructure:
    """Extract one Task 01 ``SourceFile`` without reading or executing it."""
    path = source_file.path
    language = source_file.language
    text = source_file.text
    try:
        if language == "python" or path.lower().endswith(".py"):
            return extract_python(path, text)
        if language in {"javascript", "typescript"}:
            return extract_typescript(path, text)
        return _skipped(path, language, text, f"unsupported extraction language: {language}")
    except Exception as exception:
        logger.warning(
            "internal extractor error for %s: %s",
            path,
            exception,
        )
        logger.debug("internal extractor traceback for %s", path, exc_info=True)
        return _skipped(
            path,
            language,
            text,
            f"internal extractor error: {exception.__class__.__name__}: {exception}",
        )


def extract_all(files: Iterable[SourceFile]) -> dict[str, FileStructure]:
    """Extract files in path order and return a path-keyed result."""
    results = {source_file.path: extract_file(source_file) for source_file in files}
    return {path: results[path] for path in sorted(results)}


__all__ = ["FileStructure", "extract_all", "extract_file"]
