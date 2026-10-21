"""Command-line interface. The only module that writes to the terminal."""

from __future__ import annotations

from importlib.resources import files as package_files
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import threading
import time
from typing import Annotated, NoReturn

import typer

from codewhy import __version__
from codewhy.config import CONFIG_FILE_NAME, Config, ConfigError, render_toml, resolve_config
from codewhy.consent import ConsentError, ensure_remote_ack
from codewhy.ignore import TOOLIGNORE_FILE_NAME, DiscoveryError
from codewhy.graph.builder import imported_by, imports_of, most_depended_on
from codewhy.llm.budget import prompt_budget
from codewhy.llm.provider import ProviderError, build_provider
from codewhy.pipeline import run_pipeline
from codewhy.project import build_project
from codewhy.render.docs_tree import OutputError, render_docs_tree, write_outputs
from codewhy.render.single_file import render_single_file

app = typer.Typer(
    name="codewhy",
    help="Explain a local Python/JS/TS codebase as paste-ready Markdown.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
config_app = typer.Typer(help="Inspect configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")

# (packaged template name, file written into the project)
INIT_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("codewhy.toml", CONFIG_FILE_NAME),
    ("toolignore", TOOLIGNORE_FILE_NAME),
)

PathArg = Annotated[Path, typer.Argument(help="Project folder.", show_default=True)]
ProviderOpt = Annotated[str | None, typer.Option(help="Override llm.provider (ollama, openai-compat, gemini).")]
ModelOpt = Annotated[str | None, typer.Option(help="Override llm.model.")]
OutputOpt = Annotated[str | None, typer.Option("--output", help="Output mode: single-file, docs-tree, or both.")]


def fail(message: str) -> NoReturn:
    """Print one error line to stderr and exit 1 (no traceback)."""
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(1)


def require_dir(path: Path) -> Path:
    if not path.exists():
        fail(f"path does not exist: {path}")
    if not path.is_dir():
        fail(f"path is not a directory: {path}")
    return path


def load_or_fail(root: Path, overrides: dict[str, object] | None = None) -> Config:
    try:
        return resolve_config(root, overrides)
    except ConfigError as exc:
        fail(str(exc))


def template_bytes(name: str) -> bytes:
    return package_files("codewhy").joinpath("templates", name).read_bytes()


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"codewhy {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", help="Show the version and exit.", callback=_version_callback, is_eager=True),
    ] = False,
) -> None:
    """Explain a local Python/JS/TS codebase as paste-ready Markdown."""


@app.command()
def init(path: PathArg = Path(".")) -> None:
    """Write codewhy.toml and .toolignore into PATH (never overwrites)."""
    root = require_dir(path)
    for template, target_name in INIT_TEMPLATES:
        target = root / target_name
        try:
            with target.open("xb") as handle:  # "x": fail rather than overwrite
                handle.write(template_bytes(template))
        except FileExistsError:
            typer.echo(f"skipped {target_name} (already exists)")
            continue
        except OSError as exc:
            fail(f"cannot write {target}: {exc.strerror or exc}")
        typer.echo(f"created {target_name}")


@config_app.command("show")
def config_show(path: PathArg = Path("."), provider: ProviderOpt = None, model: ModelOpt = None) -> None:
    """Print the resolved configuration (defaults + codewhy.toml + options, presets applied)."""
    root = require_dir(path)
    config = load_or_fail(root, {"llm.provider": provider, "llm.model": model})
    source = str(config.source_path) if config.source_path else "defaults (no codewhy.toml found)"
    typer.echo(f"# codewhy configuration for {root.resolve()}")
    typer.echo(f"# source: {source}")
    typer.echo(f"# effective model: {config.llm.effective_model or 'not set (required for this provider)'}")
    typer.echo(f"# effective context_tokens: {config.llm.effective_context_tokens}")
    typer.echo(f"# effective max_output_tokens: {config.llm.effective_max_output_tokens}")
    typer.echo("")
    typer.echo(render_toml(config), nl=False)


@app.command()
def doctor(path: PathArg = Path("."), provider: ProviderOpt = None, model: ModelOpt = None) -> None:
    """Check that the configured model is reachable (preflight only; no source code is sent)."""
    root = require_dir(path)
    config = load_or_fail(root, {"llm.provider": provider, "llm.model": model})
    llm = config.llm
    try:
        target = build_provider(config)
    except ConfigError as exc:
        fail(str(exc))
    try:
        target.preflight()
    except ProviderError as exc:
        fail(str(exc))
    finally:
        close = getattr(target, "close", None)
        if close is not None:
            close()
    budget = prompt_budget(llm.effective_context_tokens, llm.effective_max_output_tokens)
    typer.echo(f"provider: {llm.provider}")
    typer.echo(f"model: {llm.effective_model}")
    typer.echo(f"endpoint: {target.endpoint_label} ({'remote' if target.is_remote else 'local'})")
    typer.echo(f"context_tokens: {llm.effective_context_tokens}")
    typer.echo(f"max_output_tokens: {llm.effective_max_output_tokens}")
    typer.echo(f"prompt budget: ~{budget} tokens (estimated)")
    typer.echo("ok: preflight passed")


@app.command()
def scan(
    path: PathArg = Path("."),
    provider: ProviderOpt = None,
    model: ModelOpt = None,
    output: OutputOpt = None,
    yes: Annotated[bool, typer.Option("--yes", help="Acknowledge sending source to a remote endpoint.")] = False,
    force: Annotated[bool, typer.Option("--force", help="Overwrite non-CodeWhy output files.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", help="Suppress per-item progress lines.")] = False,
    full: Annotated[bool, typer.Option("--full", help="Regenerate all passes (accepted as a no-op before caching).")] = False,
) -> None:
    """Document the project in PATH."""
    root = require_dir(path)
    config = load_or_fail(root, {"llm.provider": provider, "llm.model": model, "output.mode": output})
    target = None
    try:
        target = build_provider(config)
        stage_started: dict[str, float] = {}
        stage_lock = threading.Lock()

        def on_stage(stage: str, detail: str) -> None:
            if quiet:
                return
            with stage_lock:
                if detail.startswith("started"):
                    stage_started[stage] = time.perf_counter()
                    typer.echo(f"{stage}: {detail}")
                    return
                elapsed = ""
                started = stage_started.pop(stage, None)
                if started is not None:
                    elapsed = f" ({time.perf_counter() - started:.2f}s)"
                typer.echo(f"{stage}: {detail}{elapsed}")

        if not quiet:
            # Provider preflight can wait for a configured network timeout. Give
            # the user immediate feedback while keeping preflight before any
            # project discovery or source-file reads.
            typer.echo(f"checking provider {target.endpoint_label}...")
        ensure_remote_ack(
            target,
            assume_yes=yes,
            acknowledged_in_config=config.llm.acknowledge_remote,
            is_tty=sys.stdin.isatty(),
            ask=lambda message: typer.confirm(message, default=False),
            tell=typer.echo,
        )
        on_stage("provider preflight", "started")
        target.preflight()
        on_stage("provider preflight", "complete")
        project = build_project(root, config, on_stage=on_stage)
        if not project.files:
            fail("No supported source files found (supported: .py .js .jsx .mjs .cjs .ts .tsx .mts .cts)")

        total = sum(1 for structure in project.structures.values() if structure.status == "ok") + len(project.modules) + 1
        index = 0

        def on_item(pass_name: str, subject: str, status: str) -> None:
            nonlocal index
            index += 1
            if not quiet:
                typer.echo(f"[{index}/{total}] {pass_name} {subject} ({status})")

        run_result = run_pipeline(
            project, target, config, clock=lambda: datetime.now(UTC), on_item=on_item, on_stage=on_stage
        )
        targets: dict[str, str] = {}
        rendered = None
        if config.output.mode in {"single-file", "both"}:
            rendered = render_single_file(project, run_result, config, version=__version__)
            targets[config.output.single_file_name] = rendered.text
        if config.output.mode in {"docs-tree", "both"}:
            tree = render_docs_tree(project, run_result, config, version=__version__)
            prefix = config.output.dir.rstrip("/") + "/"
            targets.update({prefix + relative: text for relative, text in tree.items()})
        write_outputs(root, targets, output_dir=config.output.dir if config.output.mode in {"docs-tree", "both"} else None, force=force)
        for skip in sorted(project.skips, key=lambda item: item.path):
            typer.echo(f"skipped {skip.path} ({skip.reason})")
        skipped = len(project.skips) + run_result.counts["skipped"]
        generated = run_result.counts["generated"]
        failed = run_result.counts["failed"]
        if rendered is not None:
            typer.echo(f"{len(project.files)} files, {generated} generated, {failed} failed, {skipped} skipped. {config.output.single_file_name} ≈ {rendered.estimated_tokens} tokens (level {rendered.level}).")
            if rendered.over_budget:
                typer.echo("warning: Level 2 output exceeds the configured token budget.", err=True)
        else:
            typer.echo(f"{len(project.files)} files, {generated} generated, {failed} failed, {skipped} skipped. docs-tree output written (level 0).")
        if skipped or failed:
            raise typer.Exit(2)
    except KeyboardInterrupt:
        typer.echo("scan interrupted; no output was written.", err=True)
        raise typer.Exit(130) from None
    except (ConfigError, ConsentError, ProviderError, DiscoveryError, OutputError) as exc:
        fail(str(exc))
    finally:
        if target is not None:
            close = getattr(target, "close", None)
            if close is not None:
                close()


def echo_utf8(message: str) -> None:
    """Write inspect output as UTF-8 without using the console text encoding."""
    binary_stdout = getattr(sys.stdout, "buffer", None)
    if binary_stdout is not None:
        typer.echo(message.encode("utf-8"), file=sys.stdout, nl=False)
        return
    typer.echo(message, file=sys.stdout, nl=False)


@app.command("inspect", hidden=True)
def inspect_project(path: PathArg = Path(".")) -> None:
    """Print deterministic discovery, extraction, and graph data as JSON."""
    root = require_dir(path)
    config = load_or_fail(root)
    try:
        project = build_project(root, config)
    except (ConfigError, DiscoveryError) as exc:
        fail(str(exc))

    files: dict[str, dict[str, object]] = {}
    for file_path in sorted(project.structures):
        structure = project.structures[file_path]
        node = project.graph.nodes[file_path] if file_path in project.graph else {}
        item = structure.to_dict(include_source=False)
        item.update(
            {
                "external_imports": list(node.get("external_imports", [])),
                "imports_resolved": imports_of(project.graph, file_path) if node else [],
                "imported_by": imported_by(project.graph, file_path) if node else [],
                "unresolved_imports": list(node.get("unresolved_imports", [])),
            }
        )
        files[file_path] = item

    payload = {
        "edges": [[source, destination] for source, destination in sorted(project.graph.edges)],
        "entry_point": project.entry_point,
        "files": files,
        "modules": [{"path": module.path, "files": sorted(module.files)} for module in project.modules],
        "most_depended_on": most_depended_on(project.graph),
        "other_files": sorted(project.other_files),
        "root_name": project.name,
        "skips": [
            {"detail": skip.detail, "path": skip.path, "reason": skip.reason}
            for skip in sorted(project.skips, key=lambda item: item.path)
        ],
    }
    echo_utf8(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
