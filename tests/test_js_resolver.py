from __future__ import annotations

from typing import Any, cast

from codewhy.extraction.model import ImportInfo
from codewhy.graph.js_resolver import Resolved, Unresolved, resolve_js_import


def import_info(module: str) -> ImportInfo:
    return ImportInfo("js", module, (), 0, 1, False)


def test_relative_extensionless_import_prefers_typescript() -> None:
    result = resolve_js_import("src/main.js", import_info("./foo"), frozenset({"src/main.js", "src/foo.ts", "src/foo.js"}))

    assert result == Resolved(("src/foo.ts",))


def test_directory_import_uses_index_file() -> None:
    result = resolve_js_import("main.js", import_info("./pkg"), frozenset({"main.js", "pkg/index.tsx"}))

    assert result == Resolved(("pkg/index.tsx",))


def test_exact_extension_is_resolved() -> None:
    result = resolve_js_import("main.js", import_info("./foo.jsx"), frozenset({"main.js", "foo.jsx"}))

    assert result == Resolved(("foo.jsx",))


def test_js_esm_specifier_falls_back_to_typescript() -> None:
    result = resolve_js_import("main.js", import_info("./foo.js"), frozenset({"main.js", "foo.tsx"}))

    assert result == Resolved(("foo.tsx",))


def test_rooted_specifier_is_project_relative() -> None:
    result = resolve_js_import("src/main.js", import_info("/rooted"), frozenset({"src/main.js", "rooted.ts"}))

    assert result == Resolved(("rooted.ts",))


def test_bare_and_scoped_packages_are_external() -> None:
    paths = frozenset({"main.js"})

    assert resolve_js_import("main.js", import_info("react"), paths) == Unresolved("external", "react")
    assert resolve_js_import("main.js", import_info("@scope/pkg/sub"), paths) == Unresolved(
        "external", "@scope/pkg"
    )
    assert resolve_js_import("main.js", import_info("@/utils"), paths) == Unresolved("external", "@")


def test_non_source_relative_assets_are_unresolved() -> None:
    paths = frozenset({"main.js", "styles.css", "data.json"})

    assert resolve_js_import("main.js", import_info("./styles.css"), paths) == Unresolved(
        "relative", "./styles.css"
    )
    assert resolve_js_import("main.js", import_info("./data.json"), paths) == Unresolved(
        "relative", "./data.json"
    )


def test_relative_import_leaving_root_is_unresolved() -> None:
    result = resolve_js_import("pkg/main.js", import_info("../../outside"), frozenset({"pkg/main.js", "outside.ts"}))

    assert result == Unresolved("relative", "../../outside")


def test_malformed_module_returns_external_without_raising() -> None:
    malformed = ImportInfo("js", cast(Any, None), (), 0, 1, False)

    assert resolve_js_import("main.js", malformed, frozenset({"main.js"})) == Unresolved("external", "")


def test_directory_import_of_the_project_root_uses_root_index() -> None:
    paths = frozenset({"index.ts", "y.ts", "sub/x.ts"})

    for importer, specifier in [("y.ts", "."), ("y.ts", "./"), ("sub/x.ts", ".."), ("sub/x.ts", "../"), ("sub/x.ts", "/")]:
        assert resolve_js_import(importer, import_info(specifier), paths) == Resolved(("index.ts",)), (importer, specifier)
