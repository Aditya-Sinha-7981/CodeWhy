"""Command-line interface. The only module that writes to the terminal."""

from __future__ import annotations

from collections.abc import Callable
from importlib.resources import files as package_files
from datetime import UTC, datetime
import functools
import json
import os
from pathlib import Path
import sys
import traceback
from typing import Annotated, NoReturn, TypeVar

from rich.console import Console
from rich.table import Table
import typer

from codewhy import __version__
from codewhy.cache.db import CacheDB, CacheError
from codewhy.config import CONFIG_FILE_NAME, Config, ConfigError, render_toml, resolve_config
from codewhy.consent import ConsentError, ensure_remote_ack
from codewhy.ignore import CACHE_DIR_NAME, TOOLIGNORE_FILE_NAME, DiscoveryError
from codewhy.graph.builder import imported_by, imports_of, most_depended_on
from codewhy.llm.budget import prompt_budget
from codewhy.llm.provider import ProviderError, build_provider
from codewhy.pipeline import run_pipeline
from codewhy.plan import HEADERS, plan_footer_lines, plan_header_lines, plan_rows, plan_scan, plan_text, skipped_files
from codewhy.progress import (
    EXIT_INTERRUPTED,
    INTERRUPT_MESSAGE,
    ProgressReporter,
    collected_warnings,
    exit_code_for,
    stream_encoding,
    summary_line,
    terminal_text,
)
from codewhy.project import ProjectModel, build_project
from codewhy.render.docs_tree import OutputError, render_docs_tree, validate_output_targets, write_outputs
from codewhy.render.single_file import render_single_file
from codewhy.render.writer import remove_stale_temps

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


DEBUG_ENV = "CODEWHY_DEBUG"
Command = TypeVar("Command", bound=Callable[..., None])


def fail(message: str) -> NoReturn:
    """Print one error line to stderr and exit 1 (no traceback)."""
    say(f"error: {message}", err=True)
    raise typer.Exit(1)


def guarded(command: Command) -> Command:
    """Expected failures exit through `fail`; anything else is a short internal error.

    The traceback is shown only with CODEWHY_DEBUG=1. The message names the
    exception type, never its text (which could quote source or model output).
    """

    @functools.wraps(command)
    def run(*args: object, **kwargs: object) -> None:
        try:
            command(*args, **kwargs)
        except (typer.Exit, typer.Abort, typer.TyperException, KeyboardInterrupt):
            raise
        except BrokenPipeError:
            # The reader went away (`| head`): stop quietly, and keep Python from
            # complaining again while flushing stdout at exit.
            try:
                devnull = os.open(os.devnull, os.O_WRONLY)
                os.dup2(devnull, sys.stdout.fileno())
            except (OSError, ValueError, AttributeError):
                pass
            raise typer.Exit(1) from None
        except Exception as exc:
            if os.environ.get(DEBUG_ENV, "") not in ("", "0"):
                typer.echo(traceback.format_exc(), err=True, nl=False)
            say(
                f"error: internal error ({type(exc).__name__}). "
                f"Re-run with {DEBUG_ENV}=1 to see the traceback, and please report it.",
                err=True,
            )
            raise typer.Exit(1) from None

    return run  # type: ignore[return-value]


def require_dir(path: Path) -> Path:
    if not path.exists():
        fail(f"path does not exist: {path}")
    if not path.is_dir():
        fail(f"expected a folder, but {path} is a file")
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
@guarded
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
@guarded
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
@guarded
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


NO_SUPPORTED_FILES = "No supported source files found (supported: .py .js .jsx .mjs .cjs .ts .tsx .mts .cts)"


def say(message: str, *, err: bool = False) -> None:
    """Echo one line, degraded to what the console encoding can show (files stay UTF-8)."""
    stream = sys.stderr if err else sys.stdout
    typer.echo(terminal_text(message, stream_encoding(stream)), err=err)


def _say_warnings(warnings: list[str]) -> None:
    for message in warnings:
        say(f"warning: {message}", err=True)


def _docs_tree_targets(project: ProjectModel, config: Config) -> list[str]:
    prefix = config.output.dir.rstrip("/") + "/"
    targets = [prefix + "PROJECT_OVERVIEW.md"]
    targets += [prefix + ("_module.md" if m.path == "." else f"{m.path}/_module.md") for m in project.modules]
    targets += [prefix + path + ".md" for path, fs in project.structures.items() if fs.status == "ok"]
    return targets


@app.command()
@guarded
def scan(
    path: PathArg = Path("."),
    provider: ProviderOpt = None,
    model: ModelOpt = None,
    output: OutputOpt = None,
    full: Annotated[bool, typer.Option("--full", help="Ignore cache reads; regenerate and rewrite everything.")] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="No model calls, no writes: print the plan and estimates.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Acknowledge sending source to a remote endpoint.")] = False,
    force: Annotated[bool, typer.Option("--force", help="Overwrite output files codewhy did not generate.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", help="No progress output; errors and the summary line only.")] = False,
) -> None:
    """Document the project in PATH."""
    root = require_dir(path)
    config = load_or_fail(root, {"llm.provider": provider, "llm.model": model, "output.mode": output})
    if dry_run:
        _dry_run(root, config)
        return
    is_tty = sys.stdout.isatty()
    reporter = ProgressReporter(
        quiet=quiet,
        echo=say,
        console=Console(file=sys.stdout) if is_tty and not quiet else None,
        project_label=root.resolve().name,
    )
    target = None
    with collected_warnings() as warnings:
        try:
            target = build_provider(config)
            # Consent first and outside the spinner: it may ask on the terminal.
            ensure_remote_ack(
                target,
                assume_yes=yes,
                acknowledged_in_config=config.llm.acknowledge_remote,
                is_tty=sys.stdin.isatty(),
                ask=lambda message: typer.confirm(message, default=False),
                tell=say,
            )
            reporter.start(f"checking provider {target.endpoint_label}…")
            target.preflight()
            # A temp file left by a crashed run would otherwise be discovered as an "other file".
            remove_stale_temps(root.resolve(), config.output.dir)
            project = build_project(root, config, on_stage=reporter.on_stage)
            if not project.files:
                reporter.stop()
                fail(NO_SUPPORTED_FILES)

            # Every overwrite check runs before the cache or any output is written.
            output_targets: list[str] = []
            if config.output.mode in {"single-file", "both"}:
                output_targets.append(config.output.single_file_name)
            if config.output.mode in {"docs-tree", "both"}:
                output_targets += _docs_tree_targets(project, config)
            validate_output_targets(root, dict.fromkeys(output_targets, ""), force=force)

            skips = skipped_files(project)
            reporter.set_total(len(project.structures) - len(skips) + len(project.modules) + 1)
            run_result = run_pipeline(
                project,
                target,
                config,
                clock=lambda: datetime.now(UTC),
                on_item=reporter.on_item,
                on_stage=reporter.on_stage,
                full=full,
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
            tree_dir = config.output.dir if config.output.mode in {"docs-tree", "both"} else None
            write_outputs(root, targets, output_dir=tree_dir, force=force)
        except KeyboardInterrupt:
            reporter.stop()
            say(INTERRUPT_MESSAGE, err=True)
            raise typer.Exit(EXIT_INTERRUPTED) from None
        except (ConfigError, ConsentError, ProviderError, DiscoveryError, OutputError, CacheError) as exc:
            reporter.stop()
            _say_warnings(warnings)
            fail(str(exc))
        finally:
            reporter.stop()
            if target is not None:
                close = getattr(target, "close", None)
                if close is not None:
                    close()

    _say_warnings(warnings)
    for skip_path, reason in skips:
        say(f"skipped {skip_path} ({reason})")
    counts = run_result.counts
    if rendered is not None:
        say(summary_line(len(project.files), counts, len(skips), config.output.single_file_name, rendered.estimated_tokens, rendered.level))
        if rendered.over_budget:
            say("warning: Level 2 output exceeds the configured token budget.", err=True)
    else:
        say(summary_line(len(project.files), counts, len(skips), None, None, 0))
    code = exit_code_for(run_result, skips, fatal=False)
    if code:
        raise typer.Exit(code)


def _dry_run(root: Path, config: Config) -> None:
    """Plan only: no provider is built, nothing is prompted for, nothing is written."""
    with collected_warnings() as warnings:
        try:
            project = build_project(root, config)
        except DiscoveryError as exc:
            fail(str(exc))
        if not project.files:
            _say_warnings(warnings)
            fail(NO_SUPPORTED_FILES)
        plan = plan_scan(project, config, CacheDB(project.root / CACHE_DIR_NAME / "cache.db"))
    _say_warnings(warnings)
    if sys.stdout.isatty():
        console = Console(file=sys.stdout)
        safe = lambda text: terminal_text(text, console.encoding)  # noqa: E731
        for line in plan_header_lines(plan):
            console.print(safe(line), markup=False, highlight=False)
        table = Table()
        for number, header in enumerate(HEADERS):
            table.add_column(safe(header), justify="right" if 0 < number < len(HEADERS) - 1 else "left")
        for row in plan_rows(plan):
            table.add_row(*[safe(cell) for cell in row])
        console.print(table)
        for line in plan_footer_lines(plan):
            console.print(safe(line), markup=False, highlight=False)
        return
    for line in plan_text(plan):
        say(line)


def echo_utf8(message: str) -> None:
    """Write inspect output as UTF-8 without using the console text encoding."""
    binary_stdout = getattr(sys.stdout, "buffer", None)
    if binary_stdout is not None:
        typer.echo(message.encode("utf-8"), file=sys.stdout, nl=False)
        return
    typer.echo(message, file=sys.stdout, nl=False)


@app.command("inspect", hidden=True)
@guarded
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
