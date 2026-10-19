"""Deterministic Python extraction using the standard-library AST."""

from __future__ import annotations

import ast
from collections.abc import Iterable

from .model import ClassInfo, FileStructure, FunctionInfo, ImportInfo, UnitInfo


def _line(node: ast.AST, default: int = 1) -> int:
    return int(getattr(node, "lineno", default))


def _end_line(node: ast.AST, default: int = 1) -> int:
    return int(getattr(node, "end_lineno", _line(node, default)))


def _decorator_start(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> int:
    if node.decorator_list:
        return min(_line(item, _line(node)) for item in node.decorator_list)
    return _line(node)


def _function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    signature = f"{prefix} {node.name}({ast.unparse(node.args)})"
    if node.returns is not None:
        signature += f" -> {ast.unparse(node.returns)}"
    return signature


def _function_info(node: ast.FunctionDef | ast.AsyncFunctionDef) -> FunctionInfo:
    return FunctionInfo(
        name=node.name,
        signature=_function_signature(node),
        docstring=ast.get_docstring(node, clean=True),
        start_line=_decorator_start(node),
        end_line=_end_line(node),
        is_async=isinstance(node, ast.AsyncFunctionDef),
        decorators=tuple(ast.unparse(item) for item in node.decorator_list),
    )


def _class_info(node: ast.ClassDef) -> ClassInfo:
    methods = tuple(
        _function_info(item)
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    return ClassInfo(
        name=node.name,
        bases=tuple(ast.unparse(item) for item in node.bases),
        docstring=ast.get_docstring(node, clean=True),
        methods=methods,
        start_line=_decorator_start(node),
        end_line=_end_line(node),
        decorators=tuple(ast.unparse(item) for item in node.decorator_list),
    )


def _imports(tree: ast.AST) -> tuple[ImportInfo, ...]:
    found: list[tuple[int, int, ImportInfo]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(
                    (
                        _line(node),
                        int(getattr(node, "col_offset", 0)),
                        ImportInfo(
                            kind="python",
                            module=alias.name,
                            names=(),
                            level=0,
                            line=_line(node),
                            is_type_only=False,
                        ),
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            found.append(
                (
                    _line(node),
                    int(getattr(node, "col_offset", 0)),
                    ImportInfo(
                        kind="python",
                        module=node.module or "",
                        names=tuple(alias.name for alias in node.names),
                        level=node.level,
                        line=_line(node),
                        is_type_only=False,
                    ),
                )
            )
    found.sort(key=lambda item: (item[0], item[1]))
    return tuple(item[2] for item in found)


def _children_for_class(node: ast.ClassDef) -> tuple[UnitInfo, ...]:
    methods = [
        item
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if not methods:
        return ()
    previous_end = _line(node)
    children: list[UnitInfo] = []
    for method in methods:
        children.append(
            UnitInfo(
                name=method.name,
                kind="function",
                start_line=previous_end + 1,
                end_line=_end_line(method),
            )
        )
        previous_end = _end_line(method)
    children[-1] = UnitInfo(
        name=children[-1].name,
        kind=children[-1].kind,
        start_line=children[-1].start_line,
        end_line=_end_line(node),
    )
    return tuple(children)


def _units(tree: ast.Module, line_count: int, path: str) -> tuple[UnitInfo, ...]:
    definitions: list[tuple[ast.AST, str, str]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            definitions.append((node, node.name, "function"))
        elif isinstance(node, ast.ClassDef):
            definitions.append((node, node.name, "class"))
    if not definitions:
        return (UnitInfo(path.rsplit("/", 1)[-1], "module", 1, line_count),) if line_count else ()

    result: list[UnitInfo] = []
    previous_end = 0
    for node, name, kind in definitions:
        children = _children_for_class(node) if isinstance(node, ast.ClassDef) else ()
        result.append(
            UnitInfo(
                name=name,
                kind=kind,  # type: ignore[arg-type]
                start_line=previous_end + 1,
                end_line=_end_line(node),
                children=children,
            )
        )
        previous_end = _end_line(node)
    result[-1] = UnitInfo(
        name=result[-1].name,
        kind=result[-1].kind,
        start_line=result[-1].start_line,
        end_line=line_count,
        children=result[-1].children,
    )
    return tuple(result)


def _warning(exception: Exception, source: str) -> str:
    line = getattr(exception, "lineno", None)
    if line is None:
        line = getattr(exception, "end_lineno", None)
    if line is None:
        line = 1 if source else 0
    # SyntaxError's str() already appends "(file, line N)"; use the bare message.
    message = getattr(exception, "msg", None) or str(exception) or exception.__class__.__name__
    return f"{exception.__class__.__name__}: {message} (line {line})"


def extract_python(path: str, source: str) -> FileStructure:
    """Extract facts from normalized Python source without executing it."""
    line_count = len(source.splitlines())
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exception:
        return FileStructure(
            path=path,
            language="python",
            source_text=source,
            line_count=line_count,
            status="skipped",
            warning=_warning(exception, source),
        )

    functions = tuple(
        _function_info(node)
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    classes = tuple(_class_info(node) for node in tree.body if isinstance(node, ast.ClassDef))
    return FileStructure(
        path=path,
        language="python",
        source_text=source,
        line_count=line_count,
        functions=functions,
        classes=classes,
        imports=_imports(tree),
        top_level_units=_units(tree, line_count, path),
    )
