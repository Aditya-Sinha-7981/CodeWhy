"""Pure Markdown section builders for generated CodeWhy documentation."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime

from codewhy.extraction.model import ClassInfo, FileStructure, FunctionInfo, TypeInfo
from codewhy.graph.builder import external_dependencies, imported_by, imports_of, most_depended_on
from codewhy.pipeline import RunResult
from codewhy.project import ROOT_MODULE, Module, ProjectModel
from codewhy.summarize.schemas import FilePass, ModulePass, ProjectPass
from codewhy.text import first_sentence

FAILED = "[generation failed]"
PREAMBLE = (
    "This document describes a codebase. It was generated automatically by\n"
    "codewhy from a local folder: structural facts (definitions, dependencies,\n"
    "entry point) come directly from parsing the code, while the explanations were\n"
    "written by a language model and may contain mistakes. All paths are relative\n"
    "to the project root. Sections: Overview, Modules, Files."
)


# Lines whose first non-space character (after at most 3 spaces) would start a
# Markdown block that can outlive the paragraph: an ATX heading (`#`), a code
# fence (``` or ~~~, which swallows the rest of the document when unclosed), or
# an HTML block (`<`, e.g. an unclosed `<!--`).
_BLOCK_START = re.compile(r"^( {0,3})([#`~<])")
# A line of only `=`, `-`, `*` or `_` (spaces allowed): a setext heading
# underline or a thematic break.
_RULE_LINE = re.compile(r"^( {0,3})([=\-*_])[ =\-*_]*$")


def _escape_line(line: str) -> str:
    """One backslash before the marker character; the rendered text is unchanged."""
    match = _BLOCK_START.match(line) or _RULE_LINE.match(line)
    if match is None:
        return line
    indent = match.group(1)
    return f"{indent}\\{line[len(indent):]}"


def escape_llm_text(value: str) -> str:
    """Normalize model prose so it can never create headings, fences, or other document structure."""
    # Strip before escaping: stripping afterwards could pull an indented line to the margin.
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    return "\n".join(_escape_line(line.rstrip()) for line in text.split("\n"))


def utc_iso(value: datetime) -> str:
    """Format a pass timestamp as the architecture's UTC ISO-8601 value."""
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def frontmatter(*, version: str, source: str, generated_at: datetime) -> str:
    return "\n".join(
        ["---", f"generated_by: codewhy v{version}", f"source: {source}", f"generated_at: {utc_iso(generated_at)}", "---"]
    )


def _result_text(result: object, field: str) -> str:
    failure = getattr(result, "failure", None)
    data = getattr(result, "data", None)
    if failure is not None or data is None:
        return FAILED
    return escape_llm_text(str(getattr(data, field)))


def _bullets(values: Iterable[str], *, none: str = "None") -> str:
    items = sorted(values)
    return "\n".join(f"- `{value}`" for value in items) if items else f"- {none}"


def _file_definitions(structure: FileStructure) -> str:
    entries: list[tuple[int, str, object]] = []
    entries.extend((item.start_line, "class", item) for item in structure.classes)
    entries.extend((item.start_line, "function", item) for item in structure.functions)
    entries.extend((item.start_line, "type", item) for item in structure.types)
    lines: list[str] = []
    for _, kind, item in sorted(entries, key=lambda entry: entry[0]):
        if kind == "class":
            cls = item  # type: ignore[assignment]
            bases = f"({', '.join(cls.bases)})" if cls.bases else ""
            lines.append(f"- `class {cls.name}{bases}`")
            for method in cls.methods:
                lines.append(f"  - `{method.signature}`")
        elif kind == "function":
            function = item  # type: ignore[assignment]
            lines.append(f"- `{function.signature}`")
        else:
            type_info = item  # type: ignore[assignment]
            lines.append(f"- `{type_info.kind} {type_info.name}`")
    return "\n".join(lines) if lines else "- None"


def _module_name(path: str) -> str:
    return "(project root)" if path == ROOT_MODULE else path


def _module_result(run: RunResult, module: Module) -> object:
    return run.modules.get(module.path)


def _file_result(run: RunResult, path: str) -> object | None:
    return run.files.get(path)


def _module_edges(project: ProjectModel, module: Module) -> list[str]:
    members = set(module.files)
    return sorted(
        f"`{source}` → `{destination}`"
        for source, destination in project.graph.edges
        if source in members or destination in members
    )


def _module_external(project: ProjectModel, module: Module) -> list[str]:
    return external_dependencies(project.graph, frozenset(module.files))


def _module_overview_line(run: RunResult, module: Module) -> str:
    result = _module_result(run, module)
    return first_sentence(_result_text(result, "responsibility"))


def project_overview_sections(project: ProjectModel, run: RunResult, *, heading_offset: int = 0, minimal: bool = False) -> str:
    h2 = "#" * (2 + heading_offset)
    h3 = "#" * (3 + heading_offset)
    result = run.project
    entry = f"`{project.entry_point}`" if project.entry_point else "No conventional entry point detected."
    parts = [f"{h2} Overview", "", _result_text(result, "overview"), "", f"{h3} Architecture", "", _result_text(result, "architecture"), "", f"{h3} Entry Point", "", entry]
    if not minimal:
        parts.extend(["", f"{h3} Modules", "", *[f"- `{_module_name(module.path)}` — {_module_overview_line(run, module)}" for module in project.modules]])
        depended = most_depended_on(project.graph)
        if depended:
            parts.extend(["", f"{h3} Most depended-on files", "", *[f"- `{path}` — imported by {count} file(s)" for path, count in depended]])
        others = sorted(project.other_files)
        if others:
            parts.extend(["", f"{h3} Other files in this project", "", *[f"- `{path}`" for path in others[:100]]])
            if len(others) > 100:
                parts.append(f"- … and {len(others) - 100} more")
    return "\n".join(parts)


def module_section(project: ProjectModel, run: RunResult, module: Module, *, heading_offset: int = 0, minimal: bool = False) -> str:
    h3 = "#" * (3 + heading_offset)
    h4 = "#" * (4 + heading_offset)
    result = _module_result(run, module)
    if minimal:
        # Level 2: the module heading followed only by its Responsibility paragraph.
        return "\n".join([f"{h3} `{_module_name(module.path)}`", "", _result_text(result, "responsibility")])
    edges = "\n".join(f"- {edge}" for edge in _module_edges(project, module)) or "- None"
    parts = [
        f"{h3} `{_module_name(module.path)}`",
        "",
        f"{h4} Responsibility",
        "",
        _result_text(result, "responsibility"),
        "",
        f"{h4} How the pieces cooperate",
        "",
        _result_text(result, "how_pieces_cooperate"),
        "",
        f"{h4} Files in this module",
        "",
        _bullets(module.files),
        "",
        f"{h4} Internal dependencies",
        "",
        edges,
        "",
        f"{h4} External dependencies",
        "",
        _bullets(_module_external(project, module)),
    ]
    return "\n".join(parts)


def file_section(project: ProjectModel, run: RunResult, path: str, *, heading_offset: int = 0, compact: bool = False) -> str:
    h3 = "#" * (3 + heading_offset)
    h4 = "#" * (4 + heading_offset)
    structure = project.structures[path]
    result = _file_result(run, path)
    imports = imports_of(project.graph, path) if path in project.graph else []
    users = imported_by(project.graph, path) if path in project.graph else []
    parts = [f"{h3} `{path}`", "", f"{h4} Purpose", "", _result_text(result, "purpose"), "", f"{h4} Depends On", "", _bullets(imports), "", f"{h4} Used By", "", _bullets(users)]
    if not compact:
        parts.extend(["", f"{h4} Defined Here", "", _file_definitions(structure)])
    if not compact and result is not None and getattr(result, "data", None) is not None and isinstance(result.data, FilePass) and result.data.notes:
        parts.extend(["", f"{h4} Notes", "", escape_llm_text(result.data.notes)])
    return "\n".join(parts)


def file_minimal_line(project: ProjectModel, run: RunResult, path: str) -> str:
    result = _file_result(run, path)
    return f"- `{path}` — {first_sentence(_result_text(result, 'purpose'))}"

