"""The project pass: what the project is and how its modules fit together.

Same ladder as the module pass, over module Responsibility texts ordered by
module in-degree. The entry point is the deterministic detection result; when
it is None the prompt tells the model to say so and never to guess one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from codewhy.config import Config
from codewhy.llm.provider import Provider
from codewhy.project import module_path_of
from codewhy.summarize.module_pass import AssembledPrompt, Items, execute, fit_ladder
from codewhy.summarize.prompts import cap_other_files, project_prompt
from codewhy.summarize.result import Clock, PassResult, pass_budget
from codewhy.summarize.schemas import ProjectPass

PROJECT_CONTEXT_TOO_SMALL = "context too small for the project prompt; raise llm.context_tokens"


@dataclass(frozen=True)
class ProjectRun:
    result: PassResult
    prompt_inputs: dict[str, Any]


def module_edges_text(file_edges: Iterable[tuple[str, str]]) -> str:
    """`pkg/a -> pkg/b` lines between different modules, deduplicated and sorted."""
    pairs = {(module_path_of(source), module_path_of(target)) for source, target in file_edges}
    return "\n".join(sorted(f"{source} -> {target}" for source, target in pairs if source != target))


def assemble_project_prompt(
    root_name: str,
    entry_point: str | None,
    responsibilities: Mapping[str, str],
    module_in_degree: Mapping[str, int],
    module_edges: str,
    other_files: Sequence[str],
    *,
    budget: int,
) -> AssembledPrompt:
    others = sorted(other_files)

    def build(items: Items, edges: str, omitted: int) -> str:
        return project_prompt(root_name, entry_point, items, edges, others, omitted=omitted)

    fit = fit_ladder(sorted(responsibilities.items()), module_in_degree, module_edges, build, budget)
    inputs: dict[str, Any] = {
        "root_name": root_name,
        "entry_point": entry_point,
        "modules": [list(item) for item in fit.items] if fit else [],
        "omitted": fit.omitted if fit else len(responsibilities),
        "module_edges": fit.edges if fit else "",
        "other_files": cap_other_files(others),
    }
    if fit is None:
        return AssembledPrompt(None, inputs, PROJECT_CONTEXT_TOO_SMALL)
    return AssembledPrompt(fit.prompt, inputs)


def run_project_pass(
    root_name: str,
    entry_point: str | None,
    responsibilities: Mapping[str, str],
    module_in_degree: Mapping[str, int],
    module_edges: str,
    other_files: Sequence[str],
    *,
    provider: Provider,
    config: Config,
    clock: Clock,
) -> ProjectRun:
    assembled = assemble_project_prompt(
        root_name, entry_point, responsibilities, module_in_degree, module_edges, other_files, budget=pass_budget(config)
    )
    result = execute(assembled, ProjectPass, provider=provider, config=config, clock=clock)
    return ProjectRun(result, assembled.inputs)
