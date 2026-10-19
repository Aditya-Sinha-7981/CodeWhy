from __future__ import annotations

import pytest

import codewhy.extraction.ts_treesitter as ts_treesitter
from codewhy.extraction.ts_treesitter import extract_typescript


def test_javascript_extracts_exports_classes_jsdoc_and_literal_imports() -> None:
    source = """/** Start the application. */
export default function start(value) { return value; }
import React, { useState as state } from "react";
export { helper } from "./helper";
const loaded = require("./loaded");
const lazy = import("./lazy");
const ignored = require(moduleName);

class Widget {
    /** Fetch the current value. */
    static async get() { return loaded; }
    get value() { return state; }
    set value(next) { this.next = next; }
}
"""
    result = extract_typescript("frontend/app.jsx", source)

    assert result.status == "ok"
    assert result.language == "javascript"
    assert result.functions[0].name == "start"
    assert result.functions[0].signature == "export default function start(value)"
    assert result.functions[0].docstring == "Start the application."
    assert result.classes[0].name == "Widget"
    assert [method.name for method in result.classes[0].methods] == ["get", "value", "value"]
    assert result.classes[0].methods[0].is_async is True
    assert result.classes[0].methods[0].docstring == "Fetch the current value."
    assert [(item.module, item.line) for item in result.imports] == [
        ("react", 3),
        ("./helper", 4),
        ("./loaded", 5),
        ("./lazy", 6),
    ]
    assert result.imports[0].names == ("React", "useState as state")
    assert result.imports[1].names == ("helper",)


def test_typescript_extracts_types_and_arrow_signatures() -> None:
    source = """import type { User } from "./user";
interface User { id: string }
type UserId = string;
enum Role { Admin, User }
export const format = (user: User): UserId => user.id;
"""
    result = extract_typescript("frontend/types.ts", source)

    assert result.status == "ok"
    assert result.language == "typescript"
    assert [(item.name, item.kind) for item in result.types] == [
        ("User", "interface"),
        ("UserId", "type"),
        ("Role", "enum"),
    ]
    assert result.functions[0].signature == "export const format = (user: User): UserId =>"
    assert result.imports[0].is_type_only is True
    assert result.imports[0].module == "./user"


def test_tsx_and_jsx_parse() -> None:
    tsx = extract_typescript("view.tsx", "export default function View() { return <div />; }\n")
    jsx = extract_typescript("view.jsx", "export const View = () => <div />;\n")

    assert tsx.status == "ok"
    assert tsx.functions[0].name == "View"
    assert jsx.status == "ok"
    assert jsx.functions[0].name == "View"


@pytest.mark.parametrize("extension", [".js", ".jsx", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".tsx"])
def test_all_supported_extensions_select_a_grammar(extension: str) -> None:
    source = "export const View = () => <div />;\n" if extension in {".jsx", ".tsx"} else "export const value = 1;\n"
    result = extract_typescript("view" + extension, source)

    assert result.status == "ok"


def test_non_literal_dynamic_imports_are_ignored() -> None:
    result = extract_typescript("imports.js", "require(value); import(template);\n")

    assert result.status == "ok"
    assert result.imports == ()


def test_tree_sitter_syntax_errors_are_skipped_with_first_line() -> None:
    result = extract_typescript("broken.ts", "export function broken( {\n  return 1;\n}\n")

    assert result.status == "skipped"
    assert result.warning == "syntax errors (first at line 1)"


def test_unavailable_grammar_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(extension: str):
        raise RuntimeError("missing native grammar")

    monkeypatch.setattr(ts_treesitter, "_language_for", unavailable)
    result = extract_typescript("missing.js", "const value = 1;\n")

    assert result.status == "skipped"
    assert result.warning == "tree-sitter grammar unavailable: missing native grammar"


def test_anonymous_default_arrow_is_extracted() -> None:
    result = extract_typescript("default.js", "export default () => { return 1; };\n")

    assert result.status == "ok"
    assert [(item.name, item.signature) for item in result.functions] == [
        ("default", "export default () =>")
    ]


def test_each_variable_declarator_uses_its_own_syntax_node() -> None:
    result = extract_typescript(
        "many.js",
        "const first = () => 1, second = function(value) { return value; };\n",
    )

    assert [(item.name, item.signature) for item in result.functions] == [
        ("first", "const first = () =>"),
        ("second", "const second = function(value)"),
    ]


def test_import_type_detected_from_syntax_not_text() -> None:
    source = 'import typeorm from "typeorm";\nimport type { User } from "./user";\nimport { load } from "./load";\n'

    result = extract_typescript("db.ts", source)

    assert [(item.module, item.is_type_only) for item in result.imports] == [
        ("typeorm", False),
        ("./user", True),
        ("./load", False),
    ]


def test_abstract_and_anonymous_default_classes_are_extracted() -> None:
    source = """export abstract class Shape {
  abstract area(): number;
  describe() { return "shape"; }
}

export default class {
  run() {}
}
"""
    result = extract_typescript("shapes.ts", source)

    assert [(item.name, [method.name for method in item.methods]) for item in result.classes] == [
        ("Shape", ["describe"]),
        ("default", ["run"]),
    ]
    assert [(unit.name, unit.kind, unit.start_line, unit.end_line) for unit in result.top_level_units] == [
        ("Shape", "class", 1, 4),
        ("default", "class", 5, 8),
    ]


def test_async_comes_from_the_function_itself() -> None:
    source = """const outer = () => async () => 1;
const direct = async (value) => value;
export async function load() {}
function plain() { return async () => 1; }
class Api { async fetch() {} send() { const f = async () => 1; } }
"""
    result = extract_typescript("async.js", source)

    assert [(item.name, item.is_async) for item in result.functions] == [
        ("outer", False),
        ("direct", True),
        ("load", True),
        ("plain", False),
    ]
    assert [(item.name, item.is_async) for item in result.classes[0].methods] == [("fetch", True), ("send", False)]


def test_multiline_signature_collapses_whitespace_without_changing_tokens() -> None:
    source = """export function add(
  first: number,
  second: number,
): number {
  return first + second;
}
"""
    result = extract_typescript("math.ts", source)

    assert result.functions[0].signature == "export function add( first: number, second: number, ): number"
