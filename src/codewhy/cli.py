"""Command-line interface. The only module that writes to the terminal."""

from __future__ import annotations

from importlib.resources import files as package_files
import json
from pathlib import Path
import sys
from typing import Annotated, NoReturn

import typer

from codewhy import __version__
from codewhy.config import CONFIG_FILE_NAME, Config, ConfigError, render_toml, resolve_config
from codewhy.ignore import TOOLIGNORE_FILE_NAME, DiscoveryError
from codewhy.graph.builder import imported_by, imports_of, most_depended_on
from codewhy.project import build_project

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
def config_show(
    path: PathArg = Path("."),
    provider: Annotated[str | None, typer.Option(help="Override llm.provider.")] = None,
    model: Annotated[str | None, typer.Option(help="Override llm.model.")] = None,
) -> None:
    """Print the resolved configuration (defaults + codewhy.toml + options)."""
    root = require_dir(path)
    config = load_or_fail(root, {"llm.provider": provider, "llm.model": model})
    source = str(config.source_path) if config.source_path else "defaults (no codewhy.toml found)"
    typer.echo(f"# codewhy configuration for {root.resolve()}")
    typer.echo(f"# source: {source}")
    typer.echo(f"# effective model: {config.llm.effective_model or 'not set (required for this provider)'}")
    typer.echo(f"# effective context_tokens: {config.llm.effective_context_tokens}")
    typer.echo("")
    typer.echo(render_toml(config), nl=False)


@app.command()
def scan(path: PathArg = Path(".")) -> None:
    """Document the project in PATH. (Stub: reports discovery results only.)"""
    root = require_dir(path)
    config = load_or_fail(root)
    try:
        project = build_project(root, config)
    except DiscoveryError as exc:
        fail(str(exc))
    typer.echo(
        f"Found {len(project.files)} source files in {len(project.modules)} modules "
        f"({len(project.skips)} skipped, {len(project.other_files)} other files). "
        "The documentation pipeline is not implemented yet."
    )
    for skip in project.skips:
        typer.echo(f"skipped {skip.path} ({skip.reason})")


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
