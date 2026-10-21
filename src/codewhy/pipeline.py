"""Orchestrator: file passes, then module passes, then the project pass.

docs/ARCHITECTURE.md -> Pipeline, Layer 4. Passes run sequentially in a fixed
order: files sorted by path, modules root first then by path, the project
last. Every pass goes through `run_pass`, the single seam the cache (task 07)
wraps; `inputs` is what task 07 hashes, and for module/project passes it is the
post-ladder prompt input.

`AuthError` and `BadRequestError` from the provider propagate and abort the
run; every other per-item problem becomes a failed `PassResult`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from codewhy.config import Config
from codewhy.graph.builder import canonical_edges, external_dependencies, imported_by, imports_of, module_in_degree
from codewhy.llm.provider import Provider
from codewhy.project import ROOT_MODULE, ProjectModel
from codewhy.summarize.file_pass import run_file_pass
from codewhy.summarize.module_pass import UNAVAILABLE, assemble_module_prompt, execute
from codewhy.summarize.project_pass import assemble_project_prompt, module_edges_text
from codewhy.summarize.result import Clock, PassResult, RequestCallback, pass_budget
from codewhy.summarize.schemas import FilePass, ModulePass, ProjectPass

PROJECT_SUBJECT = ""

# on_item(pass_name, subject, status); status is "generated" or "failed" (task 07 adds "cached").
OnItem = Callable[[str, str, str], None]
OnStage = Callable[[str, str], None]


@dataclass
class RunResult:
    files: dict[str, PassResult]
    modules: dict[str, PassResult]
    project: PassResult
    counts: dict[str, int]  # generated, cached, failed, skipped


def run_pass(pass_name: str, subject: str, inputs: object, fn: Callable[[], PassResult]) -> PassResult:
    """The cache seam. In this task it only runs `fn`."""
    return fn()


def run_pipeline(
    project: ProjectModel,
    provider: Provider,
    config: Config,
    *,
    clock: Clock,
    on_item: OnItem | None = None,
    on_stage: OnStage | None = None,
) -> RunResult:
    graph = project.graph
    budget = pass_budget(config)
    counts = {"generated": 0, "cached": 0, "failed": 0, "skipped": 0}

    def record(pass_name: str, subject: str, result: PassResult) -> PassResult:
        status = "failed" if result.failure is not None else "generated"
        counts[status] += 1
        if on_item is not None:
            on_item(pass_name, subject, status)
        return result

    files: dict[str, PassResult] = {}
    file_subjects = [path for path in sorted(project.structures) if project.structures[path].status == "ok"]
    if on_stage is not None:
        on_stage("file generation", f"started: {len(file_subjects)} items")
    for path in sorted(project.structures):
        fs = project.structures[path]
        if fs.status != "ok":
            counts["skipped"] += 1
            continue
        imports = imports_of(graph, path) if path in graph else []
        users = imported_by(graph, path) if path in graph else []
        inputs = {"path": path, "source_text": fs.source_text, "imports": imports, "imported_by": users}
        request_callback: RequestCallback | None = None
        if on_stage is not None:
            request_callback = lambda status, detail, path=path: on_stage(
                f"file request {path}", f"{status}: {detail}"
            )
        fn = partial(
            run_file_pass,
            fs,
            imports,
            users,
            provider=provider,
            config=config,
            clock=clock,
            on_request=request_callback,
        )
        files[path] = record("file", path, run_pass("file", path, inputs, fn))
    if on_stage is not None:
        on_stage("file generation", f"complete: {len(file_subjects)} items")

    purposes = {
        path: result.data.purpose
        for path, result in files.items()
        if isinstance(result.data, FilePass)
    }
    modules: dict[str, PassResult] = {}
    ordered_modules = sorted(project.modules, key=lambda m: (m.path != ROOT_MODULE, m.path))
    if on_stage is not None:
        on_stage("module generation", f"started: {len(ordered_modules)} items")
    for module in ordered_modules:
        members = frozenset(module.files)
        assembled = assemble_module_prompt(
            module,
            purposes,
            canonical_edges(graph, module_files=members),
            external_dependencies(graph, members),
            {path: graph.in_degree(path) if path in graph else 0 for path in module.files},
            budget=budget,
        )
        request_callback = None
        if on_stage is not None:
            request_callback = lambda status, detail, path=module.path: on_stage(
                f"module request {path}", f"{status}: {detail}"
            )
        fn = partial(
            execute,
            assembled,
            ModulePass,
            provider=provider,
            config=config,
            clock=clock,
            on_request=request_callback,
        )
        modules[module.path] = record("module", module.path, run_pass("module", module.path, assembled.inputs, fn))
    if on_stage is not None:
        on_stage("module generation", f"complete: {len(ordered_modules)} items")

    responsibilities = {
        path: result.data.responsibility if isinstance(result.data, ModulePass) else UNAVAILABLE
        for path, result in modules.items()
    }
    assembled = assemble_project_prompt(
        project.name,
        project.entry_point,
        responsibilities,
        {module.path: module_in_degree(graph, frozenset(module.files)) for module in project.modules},
        module_edges_text(graph.edges),
        project.other_files,
        budget=budget,
    )
    request_callback = None
    if on_stage is not None:
        request_callback = lambda status, detail: on_stage("project request", f"{status}: {detail}")
    fn = partial(
        execute,
        assembled,
        ProjectPass,
        provider=provider,
        config=config,
        clock=clock,
        on_request=request_callback,
    )
    if on_stage is not None:
        on_stage("project generation", "started: 1 item")
    project_result = record("project", PROJECT_SUBJECT, run_pass("project", PROJECT_SUBJECT, assembled.inputs, fn))
    if on_stage is not None:
        on_stage("project generation", "complete: 1 item")
    return RunResult(files=files, modules=modules, project=project_result, counts=counts)
