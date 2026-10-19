from __future__ import annotations

from dataclasses import dataclass

import pytest

from codewhy.extraction import extract_all, extract_file
from codewhy.extraction.model import FileStructure, FunctionInfo


@dataclass
class LocalSourceFile:
    path: str
    language: str
    text: str
    size: int


def test_dispatch_uses_local_sourcefile_double_and_sorts_results() -> None:
    files = [
        LocalSourceFile("z.js", "javascript", "export const z = () => 1;\n", 27),
        LocalSourceFile("a.py", "python", "def a():\n    return 1\n", 24),
    ]

    results = extract_all(files)

    assert list(results) == ["a.py", "z.js"]
    assert all(result.status == "ok" for result in results.values())
    assert extract_file(files[1]).to_canonical_json() == extract_file(files[1]).to_canonical_json()


def test_models_are_deeply_immutable_and_json_is_deterministic() -> None:
    decorators = ["decorator"]
    function = FunctionInfo("f", "def f()", None, 1, 1, False, decorators)
    decorators.append("changed")
    structure = FileStructure("x.py", "python", "def f():\n", 1, functions=[function])

    assert function.decorators == ("decorator",)
    assert structure.functions == (function,)
    with pytest.raises((AttributeError, TypeError)):
        structure.functions += (function,)
    assert structure.to_canonical_json() == structure.to_canonical_json()
    assert "source_text" in structure.to_canonical_json()
    assert "source_text" not in structure.to_canonical_json(include_source=False)

    serialized = structure.to_dict()
    serialized["functions"].append({})
    serialized["functions"][0]["decorators"].append("changed")
    assert len(structure.functions) == 1
    assert structure.functions[0].decorators == ("decorator",)


def test_unexpected_extractor_error_becomes_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(path: str, source: str) -> FileStructure:
        raise RuntimeError("boom")

    monkeypatch.setattr("codewhy.extraction.extract_python", explode)
    source_file = LocalSourceFile("x.py", "python", "def x():\n    pass\n", 20)

    result = extract_file(source_file)

    assert result.status == "skipped"
    assert result.warning == "internal extractor error: RuntimeError: boom"
