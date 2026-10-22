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
import logging

from codewhy.config import Config
from codewhy.cache.db import CacheDB, CacheError, CacheIOError
from codewhy.cache.keys import FAILED_MEMBER, config_fingerprint, file_key, module_key, project_key
from codewhy.graph.builder import canonical_edges, external_dependencies, imported_by, imports_of, module_in_degree
from codewhy.llm.provider import Provider
from codewhy.project import ROOT_MODULE, ProjectModel
from codewhy.summarize.file_pass import run_file_pass
from codewhy.summarize.module_pass import UNAVAILABLE, assemble_module_prompt, execute
from codewhy.summarize.project_pass import assemble_project_prompt, module_edges_text
from codewhy.summarize.result import Clock, PassResult, RequestCallback, pass_budget
from codewhy.summarize.schemas import FilePass, ModulePass, ProjectPass

logger = logging.getLogger(__name__)

PROJECT_SUBJECT = ""

# on_item(pass_name, subject, status); status is generated, cached, or failed.
OnItem = Callable[[str, str, str], None]
OnStage = Callable[[str, str], None]


@dataclass
class RunResult:
    files: dict[str, PassResult]
    modules: dict[str, PassResult]
    project: PassResult
    counts: dict[str, int]  # generated, cached, failed, skipped


@dataclass
class _CacheCall:
    """Private cache context; the public PassResult shape remains unchanged."""

    cache: CacheDB
    key: str
    fn: Callable[[], PassResult]
    full: bool
    enabled: list[bool]
    status: str | None = None

    def __call__(self) -> PassResult:
        return self.fn()


def _cache_warning(exc: Exception) -> None:
    logger.warning("cache unavailable; continuing without cache: %s", exc)


def run_pass(pass_name: str, subject: str, inputs: object, fn: Callable[[], PassResult]) -> PassResult:
    """The cache seam: a hit skips `fn`; a successful miss is stored before returning.

    `inputs` are the pass's prompt inputs (task 05's seam); the cache key travels
    in the private `_CacheCall`. Once the pipeline has opened the cache, a cache
    error only disables caching for the rest of the run: the result is kept.
    """
    if not isinstance(fn, _CacheCall):
        return fn()
    call = fn
    if call.enabled[0] and not call.full:
        try:
            cached = call.cache.lookup(pass_name, subject, call.key)
        except CacheError as exc:
            call.enabled[0] = False
            _cache_warning(exc)
        else:
            if cached is not None:
                call.status = "cached"
                return cached
    result = call.fn()
    call.status = "failed" if result.failure is not None else "generated"
    if call.enabled[0] and result.failure is None:
        try:
            call.cache.store(pass_name, subject, call.key, result)
        except CacheError as exc:
            call.enabled[0] = False
            _cache_warning(exc)
    return result


def run_pipeline(
    project: ProjectModel,
    provider: Provider,
    config: Config,
    *,
    clock: Clock,
    on_item: OnItem | None = None,
    on_stage: OnStage | None = None,
    full: bool = False,
    use_cache: bool = True,
) -> RunResult:
    """Run the three summarization stages and always close the cache."""
    cache = CacheDB(project.root / ".codewhy" / "cache.db")
    try:
        return _run_pipeline(
            project,
            provider,
            config,
            cache=cache,
            clock=clock,
            on_item=on_item,
            on_stage=on_stage,
            full=full,
            use_cache=use_cache,
        )
    finally:
        cache.close()


def _run_pipeline(
    project: ProjectModel,
    provider: Provider,
    config: Config,
    *,
    cache: CacheDB,
    clock: Clock,
    on_item: OnItem | None = None,
    on_stage: OnStage | None = None,
    full: bool = False,
    use_cache: bool = True,
) -> RunResult:
    cache_enabled = [use_cache]
    if use_cache:
        # Open before the first provider call: a locked or unwritable cache is a
        # fatal error (exit 1) that must not cost a paid request first.
        try:
            cache.ensure_open()
        except CacheIOError as exc:
            cache_enabled[0] = False
            _cache_warning(exc)
    config_fp = config_fingerprint(
        {
            "provider": config.llm.provider,
            "model": config.llm.effective_model,
            "context_tokens": config.llm.effective_context_tokens,
            "max_output_tokens": config.llm.effective_max_output_tokens,
            "temperature": config.llm.temperature,
            "seed": config.llm.seed,
            "reasoning_effort": config.llm.reasoning_effort,
            "structured_output": config.llm.structured_output,
        }
    )
    graph = project.graph
    budget = pass_budget(config)
    counts = {"generated": 0, "cached": 0, "failed": 0, "skipped": 0}

    def record(pass_name: str, subject: str, result: PassResult, status: str | None = None) -> PassResult:
        status = status or ("failed" if result.failure is not None else "generated")
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
        call = _CacheCall(cache, file_key(config_fp, path, fs.source_text, imports, users), fn, full, cache_enabled)
        files[path] = record("file", path, run_pass("file", path, inputs, call), call.status)
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
        key_files = [
            [path, FAILED_MEMBER if path in files and files[path].failure is not None else text]
            for path, text in assembled.inputs.get("files", [])
        ]
        key = module_key(
            config_fp,
            assembled.inputs.get("module", module.path),
            key_files,
            assembled.inputs.get("edges", ""),
            assembled.inputs.get("external", []),
            assembled.inputs.get("omitted", 0),
        )
        call = _CacheCall(cache, key, fn, full, cache_enabled)
        modules[module.path] = record("module", module.path, run_pass("module", module.path, assembled.inputs, call), call.status)
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
    key = project_key(
        config_fp,
        assembled.inputs.get("entry_point"),
        assembled.inputs.get("modules", []),
        canonical_edges(graph),
        assembled.inputs.get("other_files", []),
        assembled.inputs.get("omitted", 0),
    )
    call = _CacheCall(cache, key, fn, full, cache_enabled)
    project_result = record("project", PROJECT_SUBJECT, run_pass("project", PROJECT_SUBJECT, assembled.inputs, call), call.status)
    if on_stage is not None:
        on_stage("project generation", "complete: 1 item")
    if cache_enabled[0]:
        try:
            cache.delete_missing("file", set(files))
            cache.delete_missing("module", set(modules))
            cache.delete_missing("project", {PROJECT_SUBJECT})
        except CacheError as exc:
            _cache_warning(exc)
    return RunResult(files=files, modules=modules, project=project_result, counts=counts)
