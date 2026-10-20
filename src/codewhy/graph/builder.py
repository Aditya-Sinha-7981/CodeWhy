"""Dependency graph construction and deterministic graph queries."""

from __future__ import annotations

from collections.abc import Mapping

import networkx as nx

from codewhy.extraction.model import FileStructure

from .js_resolver import Resolved as JsResolved
from .js_resolver import resolve_js_import
from .python_resolver import Resolved as PythonResolved
from .python_resolver import resolve_python_import


def _paths_for_language(structures: Mapping[str, FileStructure], language: str) -> frozenset[str]:
    return frozenset(
        path
        for path, structure in structures.items()
        if structure.status == "ok" and structure.language == language
    )


def build_graph(structures: Mapping[str, FileStructure]) -> nx.DiGraph:
    graph = nx.DiGraph()
    for path in sorted(structures):
        structure = structures[path]
        if structure.status != "ok":
            continue
        graph.add_node(
            path,
            language=structure.language,
            external_imports=[],
            unresolved_imports=[],
        )

    python_paths = _paths_for_language(structures, "python")
    js_paths = frozenset(
        path
        for path, structure in structures.items()
        if structure.status == "ok" and structure.language in {"javascript", "typescript"}
    )
    for path in sorted(graph.nodes):
        structure = structures[path]
        resolver_paths = python_paths if structure.language == "python" else js_paths
        external: set[str] = set()
        unresolved: set[str] = set()
        for imp in structure.imports:
            if structure.language == "python":
                resolution = resolve_python_import(path, imp, resolver_paths)
                resolved_type = PythonResolved
            else:
                resolution = resolve_js_import(path, imp, resolver_paths)
                resolved_type = JsResolved
            if isinstance(resolution, resolved_type):
                for target in resolution.targets:
                    if target != path and target in graph:
                        graph.add_edge(path, target)
            elif resolution.kind == "external":
                if resolution.name:
                    external.add(resolution.name)
            else:
                unresolved.add(resolution.name)
        graph.nodes[path]["external_imports"] = sorted(external)
        graph.nodes[path]["unresolved_imports"] = sorted(unresolved)
    return graph


def imports_of(graph: nx.DiGraph, path: str) -> list[str]:
    return sorted(graph.successors(path))


def imported_by(graph: nx.DiGraph, path: str) -> list[str]:
    return sorted(graph.predecessors(path))


def canonical_edges(graph: nx.DiGraph, *, module_files: frozenset[str] | None = None) -> str:
    edges = [
        f"{source} -> {destination}"
        for source, destination in graph.edges
        if module_files is None or source in module_files or destination in module_files
    ]
    return "\n".join(sorted(edges))


def most_depended_on(graph: nx.DiGraph, n: int = 5) -> list[tuple[str, int]]:
    ranked = [(path, graph.in_degree(path)) for path in graph.nodes if graph.in_degree(path) > 0]
    return sorted(ranked, key=lambda item: (-item[1], item[0]))[:n]


def module_in_degree(graph: nx.DiGraph, module_files: frozenset[str]) -> int:
    return sum(1 for source, destination in graph.edges if destination in module_files and source not in module_files)


def external_dependencies(graph: nx.DiGraph, module_files: frozenset[str]) -> list[str]:
    values = {
        dependency
        for path in module_files
        if path in graph
        for dependency in graph.nodes[path].get("external_imports", [])
    }
    return sorted(values)
