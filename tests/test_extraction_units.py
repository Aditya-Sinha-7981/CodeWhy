from __future__ import annotations

from pathlib import Path

from codewhy.extraction.python_ast import extract_python
from codewhy.extraction.ts_treesitter import extract_typescript


def _assert_contiguous(units, line_count: int) -> None:
    if line_count == 0:
        assert units == ()
        return
    assert units
    assert units[0].start_line == 1
    assert units[-1].end_line == line_count
    for previous, current in zip(units, units[1:]):
        assert previous.end_line + 1 == current.start_line


def _assert_children_contiguous(unit) -> None:
    if not unit.children:
        return
    assert all(child.start_line <= child.end_line for child in unit.children)
    for previous, current in zip(unit.children, unit.children[1:]):
        assert previous.end_line + 1 == current.start_line
    for child in unit.children:
        _assert_children_contiguous(child)


def test_units_cover_committed_fixtures() -> None:
    fixture_root = Path(__file__).parent / "fixtures"
    source_paths = sorted(
        path
        for path in fixture_root.rglob("*")
        if path.suffix.lower() in {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}
    )
    for path in source_paths:
        source = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            result = extract_python(path.as_posix(), source)
        else:
            result = extract_typescript(path.as_posix(), source)
        if result.status == "ok":
            _assert_contiguous(result.top_level_units, result.line_count)
            for unit in result.top_level_units:
                _assert_children_contiguous(unit)


def test_definition_free_and_empty_files_have_expected_units() -> None:
    definition_free = extract_python("data.py", "import os\nvalue = 1\n")
    empty = extract_python("empty.py", "")

    assert [(unit.name, unit.kind, unit.start_line, unit.end_line) for unit in definition_free.top_level_units] == [("data.py", "module", 1, 2)]
    javascript = extract_typescript("data.js", "const value = 1;\n")
    assert [(unit.name, unit.kind) for unit in javascript.top_level_units] == [("data.js", "module")]
    assert empty.top_level_units == ()


def test_trailing_statements_extend_last_unit() -> None:
    result = extract_typescript("x.js", "import x from 'x';\nfunction f() {}\nconst value = 1;\n")

    assert [(unit.start_line, unit.end_line) for unit in result.top_level_units] == [(1, 3)]
