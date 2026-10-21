"""Prompt templates for every pass (docs/ARCHITECTURE.md -> Prompt rules).

Every renderer here is pure and deterministic: the same inputs give the same
text, byte for byte, because task 07's cache and task 08's dry-run depend on it.

Bump `PROMPT_VERSION` by hand whenever a template here, a schema description in
`schemas.py`, or the chunking behavior changes; the cache keys on it.

Each prompt has the same sections in the same order: role, the no-invention
rule (verbatim), output format (with the schema's field descriptions, so the
instruction also reaches providers that do not enforce schemas), task, inputs.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from codewhy.extraction.model import FileStructure
from codewhy.llm.chunking import Chunk
from codewhy.summarize.schemas import FilePass, ModulePass, ProjectPass
from codewhy.text import first_sentence

PROMPT_VERSION = 1

ROLE = (
    "You are a careful software engineer documenting a codebase for a reader who is new to it. "
    "The reader may later paste your explanation into a chat, so it must stand on its own."
)
NO_INVENTION_RULE = (
    "Do not invent behavior, APIs, or functionality not evidenced in the provided structure or source. "
    "If intent cannot be confidently established, say so plainly rather than fabricating a plausible explanation."
)
FORMAT_RULE = (
    "Reply with a JSON object only. Every value is plain prose: no headings, no Markdown lists, "
    "no code fences."
)
NO_ENTRY_POINT_INSTRUCTION = (
    "No conventional entry point was detected for this project. Say plainly that no conventional "
    "entry point was detected, and do not guess or infer one."
)
ROOT_NOTE = 'A module path of "." means the project root folder.'
NONE = "(none)"
OTHER_FILES_CAP = 50


# --- shared pieces ----------------------------------------------------------------


def _fields(model: type[BaseModel]) -> str:
    lines = []
    for name, field in model.model_fields.items():
        need = "required" if field.is_required() else "optional"
        lines.append(f'- "{name}" ({need}): {field.description}')
    return "\n".join(lines)


def _header(model: type[BaseModel], task: str) -> str:
    return "\n\n".join(
        [
            ROLE,
            NO_INVENTION_RULE,
            f"{FORMAT_RULE} The object has these fields:\n{_fields(model)}",
            f"Task: {task}",
        ]
    )


def _paths(paths: Sequence[str]) -> str:
    return "\n".join(f"- {path}" for path in paths) if paths else NONE


def _section(title: str, body: str) -> str:
    return f"## {title}\n{body}"


def _prompt(model: type[BaseModel], task: str, sections: list[tuple[str, str]]) -> str:
    inputs = "\n\n".join(_section(title, body) for title, body in sections)
    return f"{_header(model, task)}\n\nInputs:\n\n{inputs}\n"


def longest_backtick_run(text: str) -> int:
    longest = run = 0
    for char in text:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    return longest


def _fence(text: str) -> str:
    # A fence longer than any backtick run in the text, so source cannot close it early.
    fence = "`" * max(3, longest_backtick_run(text) + 1)
    body = text if text.endswith("\n") or not text else text + "\n"
    return f"{fence}\n{body}{fence}"


def _file_facts(fs: FileStructure, imports: Sequence[str], imported_by: Sequence[str]) -> list[tuple[str, str]]:
    return [
        ("File", f"path: {fs.path}\nlanguage: {fs.language}\nlines: {fs.line_count}"),
        ("Project files this file imports", _paths(imports)),
        ("Project files that import this file", _paths(imported_by)),
    ]


def top_level_names(fs: FileStructure) -> list[str]:
    """Names of the file's top-level definitions, in source order."""
    return [unit.name for unit in fs.top_level_units if unit.kind != "module"]


# --- structure -------------------------------------------------------------------------


def _doc(docstring: str | None) -> str:
    sentence = first_sentence(docstring or "")
    return f" — {sentence}" if sentence else ""


def render_structure(fs: FileStructure) -> str:
    """A compact listing of the file's classes, functions and types, in source order."""
    entries: list[tuple[int, int, str]] = []
    for cls in fs.classes:
        bases = f"({', '.join(cls.bases)})" if cls.bases else ""
        lines = [f"class {cls.name}{bases}{_doc(cls.docstring)}"]
        lines += [f"  {method.signature}{_doc(method.docstring)}" for method in sorted(cls.methods, key=lambda m: m.start_line)]
        entries.append((cls.start_line, 0, "\n".join(lines)))
    for function in fs.functions:
        entries.append((function.start_line, 1, f"{function.signature}{_doc(function.docstring)}"))
    for kind in fs.types:
        entries.append((kind.start_line, 2, f"{kind.kind} {kind.name}"))
    if not entries:
        return NONE
    return "\n".join(text for _line, _order, text in sorted(entries))


# --- file, chunk, combine --------------------------------------------------------------

FILE_TASK = (
    "Explain in plain language why this file exists in this project, given which project files "
    "import it and which it imports: what problem it solves and what role it plays for the code that "
    "uses it. Do not list its functions or restate their names; the reader already sees those."
)
CHUNK_TASK = (
    "This is one part of a file that is too large to send at once. Explain in plain language what this "
    "part contributes to the file and why it is needed. Do not list function names; the reader already sees them."
)
COMBINE_TASK = (
    "A large file was summarized in parts. Using the part summaries below, explain in plain language why "
    "the whole file exists in this project, given which project files import it and which it imports. "
    "Do not list its functions or restate their names."
)


def file_prompt(fs: FileStructure, imports: Sequence[str], imported_by: Sequence[str], *, include_source: bool) -> str:
    sections = _file_facts(fs, imports, imported_by)
    sections.append(("Structure (extracted, in source order)", render_structure(fs)))
    if include_source:
        sections.append(("Full source", _fence(fs.source_text)))
    return _prompt(FilePass, FILE_TASK, sections)


def chunk_prompt(
    fs: FileStructure, chunk: Chunk, all_names: Sequence[str], imports: Sequence[str], imported_by: Sequence[str]
) -> str:
    sections = _file_facts(fs, imports, imported_by)
    sections.append(("All top-level definitions in this file", ", ".join(all_names) if all_names else NONE))
    sections.append((f"This part: lines {chunk.start_line}-{chunk.end_line}", _fence(chunk.text)))
    return _prompt(FilePass, CHUNK_TASK, sections)


def combine_prompt(
    fs: FileStructure, chunk_summaries: Sequence[str], imports: Sequence[str], imported_by: Sequence[str]
) -> str:
    sections = _file_facts(fs, imports, imported_by)
    sections.append(("All top-level definitions in this file", ", ".join(top_level_names(fs)) or NONE))
    parts = "\n".join(f"Part {number}: {summary}" for number, summary in enumerate(chunk_summaries, start=1))
    sections.append(("Part summaries, in file order", parts or NONE))
    return _prompt(FilePass, COMBINE_TASK, sections)


# --- module and project ---------------------------------------------------------------------

MODULE_TASK = (
    "Explain what this folder (module) is responsible for in the project and how its files cooperate "
    "with each other and with the rest of the project. Ground every statement in the file purposes and "
    "import edges below."
)
PROJECT_TASK = (
    "Explain what this project is, and how control and data flow between its modules, grounded only in "
    "the module responsibilities, entry point and import edges below."
)


def omitted_line(count: int, noun: str) -> str:
    return f"{count} more {noun} omitted from this prompt"


def _described(items: Sequence[tuple[str, str]], omitted: int, noun: str) -> str:
    lines = [f"- {path}: {text}" for path, text in items]
    if omitted:
        lines.append(omitted_line(omitted, noun))
    return "\n".join(lines) if lines else NONE


def module_prompt(
    module_path: str,
    files: Sequence[tuple[str, str]],
    edges: str,
    external: Sequence[str],
    *,
    omitted: int = 0,
) -> str:
    sections = [
        ("Module", f"path: {module_path}\n{ROOT_NOTE}"),
        ("Files in this module, with each file's purpose", _described(files, omitted, "files")),
        ("Import edges involving this module (importer -> imported)", edges or NONE),
        ("External packages used", ", ".join(external) if external else NONE),
    ]
    return _prompt(ModulePass, MODULE_TASK, sections)


def cap_other_files(paths: Sequence[str], cap: int = OTHER_FILES_CAP) -> list[str]:
    """At most `cap` paths, then a `… and N more` line."""
    shown = list(paths[:cap])
    if len(paths) > cap:
        shown.append(f"… and {len(paths) - cap} more")
    return shown


def project_prompt(
    root_name: str,
    entry_point: str | None,
    modules: Sequence[tuple[str, str]],
    module_edges: str,
    other_files: Sequence[str],
    *,
    omitted: int = 0,
) -> str:
    entry = f"Entry point: {entry_point}" if entry_point else NO_ENTRY_POINT_INSTRUCTION
    sections = [
        ("Project", f"name: {root_name}\n{entry}\n{ROOT_NOTE}"),
        ("Modules, with each module's responsibility", _described(modules, omitted, "modules")),
        ("Imports between modules (importer -> imported)", module_edges or NONE),
        ("Other (non-source) files in the project", _paths(cap_other_files(other_files))),
    ]
    return _prompt(ProjectPass, PROJECT_TASK, sections)


def retry_suffix(error: str) -> str:
    return f"\n\nYour previous response was invalid: {error}. Reply with only a JSON object matching the schema."
