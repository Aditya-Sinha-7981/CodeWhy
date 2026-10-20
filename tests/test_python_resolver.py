from __future__ import annotations

from typing import Any, cast

from codewhy.extraction.model import ImportInfo
from codewhy.graph.python_resolver import Resolved, Unresolved, resolve_python_import


def import_info(module: str, names: tuple[str, ...] = (), level: int = 0) -> ImportInfo:
    return ImportInfo("python", module, names, level, 1, False)


def test_flat_script_import_resolves_sibling_module() -> None:
    import_info = ImportInfo(
        kind="python",
        module="utils",
        names=(),
        level=0,
        line=1,
        is_type_only=False,
    )

    result = resolve_python_import(
        "main.py",
        import_info,
        frozenset({"main.py", "utils.py", "solver.py"}),
    )

    assert result == Resolved(("utils.py",))


def test_flat_script_from_import_resolves_module_file() -> None:
    result = resolve_python_import(
        "main.py",
        import_info("solver", ("solve",)),
        frozenset({"main.py", "utils.py", "solver.py"}),
    )

    assert result == Resolved(("solver.py",))


def test_relative_imports_resolve_one_and_two_levels() -> None:
    paths = frozenset(
        {
            "pkg/main.py",
            "pkg/sub/worker.py",
            "pkg/sub/sibling.py",
            "pkg/sibling.py",
            "shared.py",
        }
    )

    assert resolve_python_import("pkg/sub/worker.py", import_info("sibling", level=1), paths) == Resolved(
        ("pkg/sub/sibling.py",)
    )
    assert resolve_python_import("pkg/sub/worker.py", import_info("sibling", level=2), paths) == Resolved(
        ("pkg/sibling.py",)
    )


def test_from_dot_import_prefers_submodule_and_falls_back_to_init() -> None:
    paths = frozenset({"pkg/main.py", "pkg/submodule.py", "pkg/__init__.py"})

    assert resolve_python_import("pkg/main.py", import_info("", ("submodule",), 1), paths) == Resolved(
        ("pkg/submodule.py",)
    )
    assert resolve_python_import("pkg/main.py", import_info("", ("attribute",), 1), paths) == Resolved(
        ("pkg/__init__.py",)
    )


def test_from_dot_import_unions_submodules_and_init_fallback() -> None:
    paths = frozenset({"pkg/main.py", "pkg/submodule.py", "pkg/__init__.py"})

    result = resolve_python_import("pkg/main.py", import_info("", ("submodule", "attribute"), 1), paths)

    assert result == Resolved(("pkg/__init__.py", "pkg/submodule.py"))


def test_from_import_prefers_submodule_over_package_file() -> None:
    paths = frozenset({"main.py", "a/b.py", "a/b/c.py", "a/b/d.py"})

    result = resolve_python_import("main.py", import_info("a.b", ("c", "d")), paths)

    assert result == Resolved(("a/b/c.py", "a/b/d.py"))


def test_absolute_resolution_uses_src_root_and_longest_prefix() -> None:
    assert resolve_python_import(
        "main.py",
        import_info("pkg.mod"),
        frozenset({"main.py", "src/pkg/mod.py"}),
    ) == Resolved(("src/pkg/mod.py",))
    assert resolve_python_import(
        "main.py",
        import_info("a.b.c"),
        frozenset({"main.py", "a/b.py"}),
    ) == Resolved(("a/b.py",))


def test_absolute_search_uses_first_root_without_merging() -> None:
    paths = frozenset({"pkg/main.py", "pkg/utils.py", "utils.py", "src/utils.py"})

    result = resolve_python_import("pkg/main.py", import_info("utils"), paths)

    assert result == Resolved(("pkg/utils.py",))


def test_external_imports_use_top_level_package_name() -> None:
    paths = frozenset({"main.py"})

    assert resolve_python_import("main.py", import_info("os"), paths) == Unresolved("external", "os")
    assert resolve_python_import("main.py", import_info("numpy.linalg"), paths) == Unresolved(
        "external", "numpy"
    )


def test_unresolved_relative_imports_keep_raw_specifier() -> None:
    paths = frozenset({"pkg/main.py"})

    assert resolve_python_import("pkg/main.py", import_info("missing", level=1), paths) == Unresolved(
        "relative", ".missing"
    )
    assert resolve_python_import("main.py", import_info("pkg", level=2), paths) == Unresolved(
        "relative", "..pkg"
    )


def test_relative_import_does_not_add_parent_init_implicitly() -> None:
    paths = frozenset({"pkg/sub/main.py", "pkg/__init__.py", "pkg/target.py"})

    assert resolve_python_import("pkg/sub/main.py", import_info("target", level=2), paths) == Resolved(
        ("pkg/target.py",)
    )
    assert resolve_python_import("pkg/sub/main.py", import_info("missing", level=1), paths) == Unresolved(
        "relative", ".missing"
    )


def test_odd_imports_return_unresolved() -> None:
    paths = frozenset({"main.py"})

    assert resolve_python_import("main.py", import_info("", ("*",), 1), paths) == Unresolved("relative", ".")


def test_malformed_level_returns_external_without_raising() -> None:
    malformed = ImportInfo("python", "utils", (), cast(Any, "invalid"), 1, False)

    assert resolve_python_import("main.py", malformed, frozenset({"main.py", "utils.py"})) == Unresolved(
        "external", "utils"
    )


def test_malformed_module_returns_unresolved_without_raising() -> None:
    malformed = ImportInfo("python", cast(Any, None), (), 0, 1, False)

    assert resolve_python_import("main.py", malformed, frozenset({"main.py"})) == Unresolved("external", "")


def test_relative_from_package_import_prefers_submodule_per_name() -> None:
    paths = frozenset({"pkg/main.py", "pkg/services/__init__.py", "pkg/services/service.py"})

    assert resolve_python_import("pkg/main.py", import_info("services", ("service",), 1), paths) == Resolved(
        ("pkg/services/service.py",)
    )
    assert resolve_python_import("pkg/main.py", import_info("services", ("helper",), 1), paths) == Resolved(
        ("pkg/services/__init__.py",)
    )
    assert resolve_python_import("pkg/main.py", import_info("services", ("service", "helper"), 1), paths) == Resolved(
        ("pkg/services/__init__.py", "pkg/services/service.py")
    )


def test_relative_from_namespace_package_resolves_submodule() -> None:
    paths = frozenset({"pkg/main.py", "pkg/plugins/loader.py"})

    assert resolve_python_import("pkg/main.py", import_info("plugins", ("loader",), 1), paths) == Resolved(
        ("pkg/plugins/loader.py",)
    )
    assert resolve_python_import("pkg/main.py", import_info("plugins", ("missing",), 1), paths) == Unresolved(
        "relative", ".plugins"
    )


def test_star_imports_resolve_to_the_module_file() -> None:
    paths = frozenset({"main.py", "utils.py", "pkg/__init__.py", "pkg/mod.py"})

    assert resolve_python_import("main.py", import_info("utils", ("*",)), paths) == Resolved(("utils.py",))
    assert resolve_python_import("main.py", import_info("pkg", ("*",)), paths) == Resolved(("pkg/__init__.py",))
    assert resolve_python_import("pkg/mod.py", import_info("", ("*",), 1), paths) == Resolved(("pkg/__init__.py",))
    assert resolve_python_import("main.py", import_info("pkg", ("*",), 1), paths) == Resolved(("pkg/__init__.py",))


def test_absolute_from_namespace_package_resolves_submodule() -> None:
    paths = frozenset({"src/app/worker.py", "src/plugins/registry.py"})

    assert resolve_python_import("src/app/worker.py", import_info("plugins", ("registry",)), paths) == Resolved(
        ("src/plugins/registry.py",)
    )
    assert resolve_python_import("src/app/worker.py", import_info("plugins"), paths) == Unresolved("external", "plugins")
