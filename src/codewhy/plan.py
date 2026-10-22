"""`scan --dry-run`: what a run would do, without a provider, a model call, or a write.

File-pass numbers are exact: the same keys as a real run are checked with the
read-only `CacheDB.peek`, and the same prompts are assembled (chunk plus one
combine call for oversized files). Module/project numbers are upper bounds:
their keys and prompts depend on Purpose/Responsibility texts that may not exist
yet, so every item counts as a call, and their prompts are assembled from
placeholder texts as long as the schemas allow, through the real overflow ladder.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from codewhy.cache.db import CacheDB
from codewhy.cache.keys import file_key
from codewhy.config import Config
from codewhy.consent import loopback, url_host
from codewhy.graph.builder import canonical_edges, external_dependencies, imported_by, imports_of, module_in_degree
from codewhy.llm.budget import estimate_tokens, fits
from codewhy.llm.chunking import ContextTooSmall, plan_chunks
from codewhy.pipeline import config_fingerprint_for
from codewhy.project import ProjectModel
from codewhy.summarize.file_pass import chunk_overhead, is_effectively_empty
from codewhy.summarize.module_pass import assemble_module_prompt
from codewhy.summarize.project_pass import assemble_project_prompt, module_edges_text
from codewhy.summarize.prompts import chunk_prompt, combine_prompt, file_prompt, top_level_names
from codewhy.summarize.result import pass_budget
from codewhy.summarize.schemas import FILE_TEXT_MAX, MODULE_TEXT_MAX


@dataclass(frozen=True)
class PassPlan:
    name: str  # "file" | "module" | "project"
    items: int
    hits: int | None  # None: cannot be known without reading cached prose
    calls: int
    tokens: int
    exact: bool

    @property
    def misses(self) -> int | None:
        return None if self.hits is None else self.items - self.hits


@dataclass(frozen=True)
class Plan:
    discovered: int
    skipped: list[tuple[str, str]]
    modules: int
    entry_point: str | None
    passes: list[PassPlan]
    chunked_files: list[str] = field(default_factory=list)
    remote_host: str | None = None

    @property
    def calls(self) -> int:
        return sum(p.calls for p in self.passes)

    @property
    def tokens(self) -> int:
        return sum(p.tokens for p in self.passes)


def skipped_files(project: ProjectModel) -> list[tuple[str, str]]:
    """(path, reason) for every file skipped by discovery or extraction, sorted by path."""
    entries = [(skip.path, skip.reason) for skip in project.skips]
    entries += [
        (path, fs.warning or "skipped")
        for path, fs in project.structures.items()
        if fs.status != "ok"
    ]
    return sorted(entries)


def remote_host(config: Config) -> str | None:
    """The host source would be sent to, or None for a local Ollama (no provider is built)."""
    llm = config.llm
    if llm.provider == "ollama":
        host = url_host(llm.host)
        return None if loopback(host) else host
    return url_host(llm.base_url or "") or llm.provider


def _placeholder(length: int) -> str:
    # One sentence (no inner full stop), so first-sentence shortening keeps all of it.
    return ("word " * (length // 5 + 1))[: length - 1].rstrip() + "."


def _file_calls(fs, imports: list[str], users: list[str], budget: int) -> tuple[int, int, bool]:
    """(calls, estimated input tokens, chunked) for one uncached file."""
    if is_effectively_empty(fs.source_text, fs.language):
        return 0, 0, False
    prompt = file_prompt(fs, imports, users, include_source=True)
    if fits(prompt, budget):
        return 1, estimate_tokens(prompt), False
    names = top_level_names(fs)
    try:
        chunks = plan_chunks(fs, budget, chunk_overhead(fs, names, imports, users))
    except ContextTooSmall:
        return 0, 0, False
    tokens = sum(estimate_tokens(chunk_prompt(fs, chunk, names, imports, users)) for chunk in chunks)
    combined = combine_prompt(fs, [_placeholder(FILE_TEXT_MAX)] * len(chunks), imports, users)
    return len(chunks) + 1, tokens + estimate_tokens(combined), True


def plan_scan(project: ProjectModel, config: Config, cache: CacheDB) -> Plan:
    graph = project.graph
    budget = pass_budget(config)
    config_fp = config_fingerprint_for(config)

    file_items = hits = calls = tokens = 0
    chunked: list[str] = []
    for path in sorted(project.structures):
        fs = project.structures[path]
        if fs.status != "ok":
            continue
        file_items += 1
        imports = imports_of(graph, path) if path in graph else []
        users = imported_by(graph, path) if path in graph else []
        if cache.peek("file", path, file_key(config_fp, path, fs.source_text, imports, users)):
            hits += 1
            continue
        n, t, was_chunked = _file_calls(fs, imports, users, budget)
        calls += n
        tokens += t
        if was_chunked:
            chunked.append(path)
    passes = [PassPlan("file", file_items, hits, calls, tokens, exact=True)]

    module_calls = module_tokens = 0
    purpose = _placeholder(FILE_TEXT_MAX)
    for module in project.modules:
        members = frozenset(module.files)
        assembled = assemble_module_prompt(
            module,
            {path: purpose for path in module.files},
            canonical_edges(graph, module_files=members),
            external_dependencies(graph, members),
            {path: graph.in_degree(path) if path in graph else 0 for path in module.files},
            budget=budget,
        )
        if assembled.prompt is not None:
            module_calls += 1
            module_tokens += estimate_tokens(assembled.prompt)
    passes.append(PassPlan("module", len(project.modules), None, module_calls, module_tokens, exact=False))

    responsibility = _placeholder(MODULE_TEXT_MAX)
    assembled = assemble_project_prompt(
        project.name,
        project.entry_point,
        {module.path: responsibility for module in project.modules},
        {module.path: module_in_degree(graph, frozenset(module.files)) for module in project.modules},
        module_edges_text(graph.edges),
        project.other_files,
        budget=budget,
    )
    project_calls = 0 if assembled.prompt is None else 1
    project_tokens = 0 if assembled.prompt is None else estimate_tokens(assembled.prompt)
    passes.append(PassPlan("project", 1, None, project_calls, project_tokens, exact=False))

    return Plan(
        discovered=len(project.files) + len(project.skips),
        skipped=skipped_files(project),
        modules=len(project.modules),
        entry_point=project.entry_point,
        passes=passes,
        chunked_files=chunked,
        remote_host=remote_host(config),
    )


def _n(value: int | None) -> str:
    return "—" if value is None else f"{value:,}"


HEADERS = ("pass", "items", "hits", "misses", "calls", "est. input tokens", "")


def plan_rows(plan: Plan) -> list[tuple[str, ...]]:
    rows = [
        (p.name, _n(p.items), _n(p.hits), _n(p.misses), _n(p.calls), _n(p.tokens), "exact" if p.exact else "upper bound")
        for p in plan.passes
    ]
    rows.append(("total", "", "", "", _n(plan.calls), _n(plan.tokens), "upper bound"))
    return rows


def plan_header_lines(plan: Plan) -> list[str]:
    lines = ["Dry run: no model calls, nothing written."]
    lines.append(f"files discovered: {plan.discovered} ({len(plan.skipped)} skipped)")
    lines += [f"skipped {path} ({reason})" for path, reason in plan.skipped]
    lines.append(f"modules: {plan.modules}; entry point: {plan.entry_point or 'none detected'}")
    return lines


def plan_footer_lines(plan: Plan) -> list[str]:
    lines = [
        "Module and project numbers are upper bounds: their prompts depend on summaries that do not exist yet.",
        "Retries after malformed model output are not included.",
    ]
    if plan.chunked_files:
        lines.append(
            f"{len(plan.chunked_files)} oversized file(s) are chunked; their call counts are exact, "
            "their combine-prompt tokens are an upper bound."
        )
    if plan.remote_host is not None:
        lines.append(f"This would send ≈ {plan.tokens:,} tokens of your source to {plan.remote_host}.")
    return lines


def plan_text(plan: Plan) -> list[str]:
    """Plain, aligned lines (non-TTY output)."""
    rows = [HEADERS, *plan_rows(plan)]
    widths = [max(len(row[i]) for row in rows) for i in range(len(HEADERS))]
    table = []
    for row in rows:
        cells = [row[0].ljust(widths[0])] + [row[i].rjust(widths[i]) for i in range(1, len(row) - 1)] + [row[-1]]
        table.append("  ".join(cells).rstrip())
    return [*plan_header_lines(plan), "", *table, "", *plan_footer_lines(plan)]
