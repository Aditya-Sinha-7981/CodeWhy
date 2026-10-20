from __future__ import annotations

import networkx as nx
from pathlib import Path

from codewhy.extraction.model import FileStructure, ImportInfo
from codewhy.graph.builder import (
    build_graph,
    canonical_edges,
    external_dependencies,
    imported_by,
    imports_of,
    module_in_degree,
    most_depended_on,
)
from codewhy.config import resolve_config
from codewhy.project import build_project
from tests.helpers import make_project


def structure(path: str, language: str, imports: tuple[ImportInfo, ...] = ()) -> FileStructure:
    return FileStructure(path, language, "", 0, imports=imports)


def python_import(module: str, names: tuple[str, ...] = (), level: int = 0) -> ImportInfo:
    return ImportInfo("python", module, names, level, 1, False)


def js_import(module: str) -> ImportInfo:
    return ImportInfo("js", module, (), 0, 1, False)


def test_build_graph_resolves_edges_and_external_metadata() -> None:
    structures = {
        "main.py": structure("main.py", "python", (python_import("utils"), python_import("os"))),
        "utils.py": structure("utils.py", "python"),
        "web.js": structure("web.js", "javascript", (js_import("react"), js_import("./component"))),
        "component.ts": structure("component.ts", "typescript"),
        "broken.py": FileStructure("broken.py", "python", "", 0, status="skipped", warning="bad"),
    }

    graph = build_graph(structures)

    assert set(graph.nodes) == {"main.py", "utils.py", "web.js", "component.ts"}
    assert set(graph.edges) == {("main.py", "utils.py"), ("web.js", "component.ts")}
    assert graph.nodes["main.py"]["external_imports"] == ["os"]
    assert graph.nodes["web.js"]["external_imports"] == ["react"]
    assert "broken.py" not in graph


def test_graph_keeps_cycles_drops_self_edges_and_is_deterministic() -> None:
    structures = {
        "a.py": structure("a.py", "python", (python_import("b"), python_import("a"))),
        "b.py": structure("b.py", "python", (python_import("a"),)),
        "z.py": structure("z.py", "python"),
    }

    graph = build_graph(structures)

    assert set(graph.edges) == {("a.py", "b.py"), ("b.py", "a.py")}
    assert canonical_edges(graph) == "a.py -> b.py\nb.py -> a.py"
    assert canonical_edges(nx.DiGraph([("b.py", "a.py"), ("a.py", "b.py")])) == canonical_edges(graph)


def test_graph_queries_and_module_derived_data_are_sorted() -> None:
    graph = nx.DiGraph()
    graph.add_nodes_from(
        [
            ("a.py", {"external_imports": ["os"], "unresolved_imports": []}),
            ("b.py", {"external_imports": ["numpy"], "unresolved_imports": []}),
            ("c.py", {"external_imports": ["os"], "unresolved_imports": []}),
            ("d.py", {"external_imports": [], "unresolved_imports": []}),
        ]
    )
    graph.add_edges_from(
        [("a.py", "d.py"), ("b.py", "d.py"), ("c.py", "d.py"), ("a.py", "b.py"), ("c.py", "b.py")]
    )

    assert imports_of(graph, "a.py") == ["b.py", "d.py"]
    assert imported_by(graph, "d.py") == ["a.py", "b.py", "c.py"]
    assert most_depended_on(graph) == [("d.py", 3), ("b.py", 2)]
    assert module_in_degree(graph, frozenset({"a.py", "b.py"})) == 1
    assert external_dependencies(graph, frozenset({"a.py", "b.py"})) == ["numpy", "os"]
    assert canonical_edges(graph, module_files=frozenset({"b.py"})) == (
        "a.py -> b.py\n"
        "b.py -> d.py\n"
        "c.py -> b.py"
    )


def test_python_js_mixed_fixture_has_no_cross_language_edges() -> None:
    structures = {
        "main.py": structure("main.py", "python", (python_import("utils"),)),
        "utils.py": structure("utils.py", "python"),
        "web.js": structure("web.js", "javascript", (js_import("./component"),)),
        "component.ts": structure("component.ts", "typescript"),
    }

    graph = build_graph(structures)

    assert set(graph.edges) == {("main.py", "utils.py"), ("web.js", "component.ts")}


def test_nested_fixture_has_expected_full_edge_list() -> None:
    root = Path(__file__).parent / "fixtures" / "nested_modules"
    project = build_project(root, resolve_config(root))

    assert canonical_edges(project.graph) == "\n".join(
        [
            "src/app/__init__.py -> src/app/core.py",
            "src/app/api/__init__.py -> src/app/api/routes.py",
            "src/app/api/routes.py -> src/app/services/service.py",
            "src/app/api/routes.py -> src/config.py",
            "src/app/cli.py -> src/app/api/routes.py",
            "src/app/cli.py -> src/app/core.py",
            "src/app/core.py -> src/app/services/service.py",
            "src/app/core.py -> src/app/utils.py",
            "src/app/services/__init__.py -> src/app/services/service.py",
            "src/app/services/service.py -> src/app/core.py",
            "src/app/services/service.py -> src/app/utils.py",
            "src/app/utils.py -> src/app/models.py",
            "src/app/worker.py -> src/app/api/routes.py",
            "src/app/worker.py -> src/app/services/service.py",
            "src/app/worker.py -> src/plugins/registry.py",
            "src/plugins/loader.py -> src/app/core.py",
            "src/plugins/registry.py -> src/plugins/loader.py",
            "web/components/Button.tsx -> web/shared.ts",
            "web/components/index.ts -> web/components/Button.tsx",
            "web/main.js -> web/components/index.ts",
        ]
    )
    assert most_depended_on(project.graph) == [
        ("src/app/core.py", 4),
        ("src/app/services/service.py", 4),
        ("src/app/api/routes.py", 3),
        ("src/app/utils.py", 2),
        ("src/app/models.py", 1),
    ]
    assert external_dependencies(project.graph, frozenset(project.graph.nodes)) == ["os", "react", "requests"]
