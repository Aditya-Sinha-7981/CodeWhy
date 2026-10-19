from __future__ import annotations

from pathlib import Path

import pytest

from codewhy.config import resolve_config
from codewhy.extraction import extract_all, extract_file
from codewhy.extraction.model import FileStructure, FunctionInfo
from codewhy.ignore import SourceFile
from codewhy.project import build_project
from tests.fixtures.builders import (
    BROKEN_FILES_DISCOVERY_SKIPS,
    BROKEN_FILES_EMPTY,
    BROKEN_FILES_HEALTHY,
    BROKEN_FILES_SYNTAX_ERROR,
    build_broken_files,
)
from tests.helpers import make_project

FIXTURES = Path(__file__).parent / "fixtures"


def test_dispatch_sorts_results() -> None:
    files = [
        SourceFile("z.js", "javascript", "export const z = () => 1;\n", 27),
        SourceFile("a.py", "python", "def a():\n    return 1\n", 24),
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
    source_file = SourceFile("x.py", "python", "def x():\n    pass\n", 20)

    result = extract_file(source_file)

    assert result.status == "skipped"
    assert result.warning == "internal extractor error: RuntimeError: boom"


@pytest.mark.parametrize(
    ("path", "language"),
    [
        ("a.py", "python"),
        ("a.js", "javascript"),
        ("a.jsx", "javascript"),
        ("a.mjs", "javascript"),
        ("a.cjs", "javascript"),
        ("a.ts", "typescript"),
        ("a.tsx", "typescript"),
        ("a.mts", "typescript"),
        ("a.cts", "typescript"),
    ],
)
def test_dispatch_by_extension(tmp_path: Path, path: str, language: str) -> None:
    root = make_project(tmp_path, {path: "export {};\n" if language != "python" else "x = 1\n"})
    (source,) = build_project(root, resolve_config(root)).files.values()

    result = extract_file(source)

    assert (result.status, result.language) == ("ok", language)


def test_broken_files_through_real_discovery(tmp_path: Path) -> None:
    root = build_broken_files(tmp_path, max_file_bytes=64)
    project = build_project(root, resolve_config(root, {"extraction.max_file_bytes": 64}))

    results = extract_all(project.files.values())

    # Discovery owns size/decoding skips; the symlink loop is never followed.
    assert [(skip.path, skip.reason) for skip in project.skips] == BROKEN_FILES_DISCOVERY_SKIPS
    assert sorted(results) == sorted([*BROKEN_FILES_HEALTHY, BROKEN_FILES_EMPTY, BROKEN_FILES_SYNTAX_ERROR])
    # Extraction owns parse failures.
    skipped = {path: result.warning for path, result in results.items() if result.status == "skipped"}
    assert skipped == {BROKEN_FILES_SYNTAX_ERROR: "SyntaxError: invalid syntax (line 1)"}
    assert results[BROKEN_FILES_EMPTY].top_level_units == ()
    assert all(not (r.warning or "").startswith("internal extractor error") for r in results.values())


@pytest.mark.parametrize("fixture", ["flat_scripts", "python_js_mixed"])
def test_committed_fixtures_extract_deterministically(fixture: str) -> None:
    root = FIXTURES / fixture
    project = build_project(root, resolve_config(root))

    first = {path: result.to_canonical_json() for path, result in extract_all(project.files.values()).items()}
    second = {path: result.to_canonical_json() for path, result in extract_all(project.files.values()).items()}

    assert first == second
    assert first
    assert all('"status":"ok"' in value for value in first.values())


def test_python_js_mixed_covers_every_import_form() -> None:
    root = FIXTURES / "python_js_mixed"
    results = extract_all(build_project(root, resolve_config(root)).files.values())

    modules = {(path, item.module) for path, result in results.items() for item in result.imports}

    for expected in [
        ("backend/app.py", "db"),  # Python relative import
        ("frontend/main.js", "react"),  # bare package, ESM
        ("frontend/main.js", "./styles.css"),  # non-source relative
        ("frontend/main.js", "./lazy"),  # dynamic import()
        ("frontend/components/index.ts", "./Panel"),  # export ... from
        ("frontend/App.jsx", "./components"),  # directory import
        ("frontend/App.jsx", "./utils.js"),  # .js specifier for a .ts file
        ("frontend/App.jsx", "@/api/client"),  # alias import
    ]:
        assert expected in modules
    assert results["frontend/components/Panel.tsx"].language == "typescript"
    assert any(i.module == "react" and i.line == 5 for i in results["frontend/main.js"].imports)  # require()
