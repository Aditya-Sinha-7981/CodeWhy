from __future__ import annotations

from codewhy.extraction.model import FileStructure
from codewhy.extraction.python_ast import extract_python


def test_python_extracts_top_level_definitions_and_whole_tree_imports() -> None:
    source = '''from __future__ import annotations
import os
from .pkg import item as renamed

@decorator(value=1)
async def work(value: int = 1, *args: str, **kwargs: int) -> str:
    """Work documentation."""
    import inside
    return value

class Thing(Base):
    """Thing documentation."""
    @staticmethod
    def make() -> "Thing":
        return Thing()

    def method(self):
        def nested():
            return None
        return nested()
'''
    result = extract_python("pkg/mod.py", source)

    assert result.status == "ok"
    assert [item.name for item in result.functions] == ["work"]
    assert result.functions[0].signature == "async def work(value: int=1, *args: str, **kwargs: int) -> str"
    assert result.functions[0].decorators == ("decorator(value=1)",)
    assert result.functions[0].docstring == "Work documentation."
    assert [item.name for item in result.classes] == ["Thing"]
    assert [item.name for item in result.classes[0].methods] == ["make", "method"]
    assert all(item.kind == "python" for item in result.imports)
    assert [(item.module, item.level, item.names) for item in result.imports] == [
        ("__future__", 0, ("annotations",)),
        ("os", 0, ()),
        ("pkg", 1, ("item",)),
        ("inside", 0, ()),
    ]
    assert result.types == ()


def test_python_units_are_contiguous_and_nested_functions_are_not_listed() -> None:
    source = """# leading
import one

def first():
    def nested():
        pass
    return nested()

value = 1

class Box:
    def get(self):
        return value

trailing = 2
"""
    result = extract_python("box.py", source)

    assert [(unit.name, unit.start_line, unit.end_line) for unit in result.top_level_units] == [
        ("first", 1, 7),
        ("Box", 8, 15),
    ]
    assert [(unit.name, unit.start_line, unit.end_line) for unit in result.top_level_units[1].children] == [
        ("get", 12, 13),
    ]
    assert [item.name for item in result.functions] == ["first"]


def test_python_parse_failures_are_skipped_with_line_information() -> None:
    result = extract_python("broken.py", "def broken(:\n    pass\n")

    assert result.status == "skipped"
    assert result.warning is not None
    assert result.warning.startswith("SyntaxError:")
    assert "line 1" in result.warning
    assert result.functions == ()


def test_python_null_bytes_are_skipped() -> None:
    result = extract_python("broken.py", "value = '\x00'\n")

    assert result.status == "skipped"
    assert result.warning is not None
    assert result.warning.startswith("SyntaxError:") or result.warning.startswith("ValueError:")


def test_empty_python_file_has_no_units() -> None:
    result = extract_python("empty.py", "")

    assert isinstance(result, FileStructure)
    assert result.line_count == 0
    assert result.top_level_units == ()
    assert result.status == "ok"


def test_imports_inside_type_checking_and_try_except_are_collected() -> None:
    source = """from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pkg.types import Token

try:
    import ujson as json
except ImportError:
    import json


def parse(text):
    from .lexer import lex
    return lex(text)
"""
    result = extract_python("parser.py", source)

    assert [(item.module, item.names, item.level, item.line) for item in result.imports] == [
        ("typing", ("TYPE_CHECKING",), 0, 1),
        ("pkg.types", ("Token",), 0, 4),
        ("ujson", (), 0, 7),
        ("json", (), 0, 9),
        ("lexer", ("lex",), 1, 13),
    ]


def test_syntax_error_warning_names_line_once() -> None:
    result = extract_python("broken.py", "x = 1\ndef broken(:\n    pass\n")

    assert result.status == "skipped"
    assert result.warning == "SyntaxError: invalid syntax (line 2)"
