"""The module pass and the overflow ladder it shares with the project pass.

docs/ARCHITECTURE.md -> `module_pass` and `project_pass` overflow ladders.
Prompt assembly is separate from the provider call: `assemble_module_prompt`
is pure (task 08's dry-run uses it), and the post-ladder inputs it returns are
what task 07 hashes into the cache key.

Ladder (stop at the first step that fits):
1. full texts; 2. first sentence of each; 3. first sentences of as many items
as fit, in in-degree order (desc, ties by path), plus an `N more … omitted`
line. If step 3 cannot fit even with no items, the edge list is cut line by
line (sorted order) with an `… and N more edges` line. If that still does not
fit, the item fails with a "context too small" reason; nothing is sent.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from codewhy.config import Config
from codewhy.llm.budget import fits
from codewhy.llm.provider import Provider
from codewhy.project import Module
from codewhy.summarize.prompts import module_prompt
from codewhy.summarize.result import Clock, PassFailed, PassResult, call_pass, pass_budget
from codewhy.summarize.schemas import ModulePass
from codewhy.text import first_sentence

UNAVAILABLE = "(summary unavailable)"
MODULE_CONTEXT_TOO_SMALL = "context too small for this module's prompt; raise llm.context_tokens"

Items = list[tuple[str, str]]
# build(items, edges_text, omitted) -> prompt
Builder = Callable[[Items, str, int], str]


@dataclass(frozen=True)
class LadderFit:
    items: Items  # what the prompt lists, sorted by path
    omitted: int
    edges: str
    prompt: str


def more_edges_line(count: int) -> str:
    return f"… and {count} more edges"


def fit_ladder(
    items: Sequence[tuple[str, str]],
    in_degree: Mapping[str, int],
    edges: str,
    build: Builder,
    budget: int,
) -> LadderFit | None:
    """The first ladder step whose prompt fits `budget`, or None when even the smallest does not."""
    full = sorted(items)
    prompt = build(full, edges, 0)
    if fits(prompt, budget):
        return LadderFit(full, 0, edges, prompt)
    short = [(path, first_sentence(text)) for path, text in full]
    prompt = build(short, edges, 0)
    if fits(prompt, budget):
        return LadderFit(short, 0, edges, prompt)

    total = len(short)
    edge_text = edges
    if not fits(build([], edges, total), budget):
        edge_text = _cut_edges(edges, lambda text: fits(build([], text, total), budget))
        if edge_text is None:
            return None
    ranked = sorted(short, key=lambda item: (-in_degree.get(item[0], 0), item[0]))
    chosen: Items = []
    for item in ranked:
        trial = sorted([*chosen, item])
        if not fits(build(trial, edge_text, total - len(trial)), budget):
            break
        chosen = trial
    return LadderFit(chosen, total - len(chosen), edge_text, build(chosen, edge_text, total - len(chosen)))


def _cut_edges(edges: str, ok: Callable[[str], bool]) -> str | None:
    """The longest prefix of the edge lines (plus a `… and N more edges` line) that `ok` accepts."""
    lines = edges.split("\n") if edges else []
    if not lines:
        return None

    def text(kept: int) -> str:
        return "\n".join([*lines[:kept], more_edges_line(len(lines) - kept)])

    # Each kept edge adds a whole line while the count shrinks by at most one
    # digit, so the prompt only grows with `kept`: binary search is exact.
    if not ok(text(0)):
        return None
    low, high = 0, max(len(lines) - 1, 0)
    while low < high:
        middle = (low + high + 1) // 2
        if ok(text(middle)):
            low = middle
        else:
            high = middle - 1
    return text(low)


@dataclass(frozen=True)
class AssembledPrompt:
    prompt: str | None  # None when nothing fits; `failure` says why
    inputs: dict[str, Any]
    failure: str | None = None


@dataclass(frozen=True)
class ModuleRun:
    result: PassResult
    prompt_inputs: dict[str, Any]


def module_texts(files: Sequence[str], purposes: Mapping[str, str]) -> Items:
    return [(path, purposes.get(path) or UNAVAILABLE) for path in sorted(files)]


def assemble_module_prompt(
    module: Module,
    purposes: Mapping[str, str],
    edges_text: str,
    external: Sequence[str],
    in_degree: Mapping[str, int],
    *,
    budget: int,
) -> AssembledPrompt:
    externals = sorted(external)

    def build(items: Items, edges: str, omitted: int) -> str:
        return module_prompt(module.path, items, edges, externals, omitted=omitted)

    fit = fit_ladder(module_texts(module.files, purposes), in_degree, edges_text, build, budget)
    if fit is None:
        inputs = {"module": module.path, "files": [], "omitted": len(module.files), "edges": "", "external": externals}
        return AssembledPrompt(None, inputs, MODULE_CONTEXT_TOO_SMALL)
    inputs = {
        "module": module.path,
        "files": [list(item) for item in fit.items],
        "omitted": fit.omitted,
        "edges": fit.edges,
        "external": externals,
    }
    return AssembledPrompt(fit.prompt, inputs)


def execute(
    assembled: AssembledPrompt,
    model_cls: type[BaseModel],
    *,
    provider: Provider,
    config: Config,
    clock: Clock,
) -> PassResult:
    """Send an assembled prompt and wrap the outcome as a PassResult."""
    if assembled.prompt is None:
        return PassResult.failed(assembled.failure or "nothing to send", clock())
    try:
        data = call_pass(
            provider,
            assembled.prompt,
            model_cls,
            budget=pass_budget(config),
            max_output_tokens=config.llm.effective_max_output_tokens,
        )
    except PassFailed as exc:
        return PassResult.failed(exc.reason, clock())
    return PassResult.ok(data, clock())


def run_module_pass(
    module: Module,
    purposes: Mapping[str, str],
    edges_text: str,
    external: Sequence[str],
    in_degree: Mapping[str, int],
    *,
    provider: Provider,
    config: Config,
    clock: Clock,
) -> ModuleRun:
    assembled = assemble_module_prompt(module, purposes, edges_text, external, in_degree, budget=pass_budget(config))
    result = execute(assembled, ModulePass, provider=provider, config=config, clock=clock)
    return ModuleRun(result, assembled.inputs)
