"""Single-file Markdown rendering and render-level selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from codewhy.config import Config
from codewhy.llm.budget import estimate_tokens
from codewhy.pipeline import RunResult
from codewhy.project import ProjectModel

from .sections import PREAMBLE, file_minimal_line, file_section, frontmatter, module_section, project_overview_sections


@dataclass(frozen=True)
class RenderedDoc:
    text: str
    level: int
    estimated_tokens: int
    over_budget: bool


def _latest_timestamp(project: ProjectModel, run: RunResult) -> datetime:
    results = [*run.files.values(), *run.modules.values(), run.project]
    if not results:
        raise ValueError("cannot render a project without pass results")
    return max(result.generated_at for result in results)


def _render(project: ProjectModel, run: RunResult, *, version: str, level: int) -> str:
    timestamp = _latest_timestamp(project, run)
    parts = [frontmatter(version=version, source="project", generated_at=timestamp), "", f"# {project.name} — codebase documentation", "", PREAMBLE, ""]
    parts.append(project_overview_sections(project, run, minimal=level == 2))
    if level < 2:
        parts.extend(["", "## Modules", "", *[module_section(project, run, module) for module in project.modules]])
        parts.extend(["", "## Files", "", *[file_section(project, run, path, compact=level == 1) for path in sorted(run.files)]])
    else:
        parts.extend(
            [
                "",
                "## Modules",
                "",
                *[module_section(project, run, module, minimal=True) for module in project.modules],
                "",
                "## Files",
                "",
                "\n".join(file_minimal_line(project, run, path) for path in sorted(run.files)),
            ]
        )
    return "\n\n".join(part for part in parts if part != "") + "\n"


def render_single_file(project: ProjectModel, run_result: RunResult, config: Config, *, version: str) -> RenderedDoc:
    """Render the smallest permitted level that fits the configured estimate."""
    for level in (0, 1, 2):
        text = _render(project, run_result, version=version, level=level)
        estimated = estimate_tokens(text)
        if estimated <= config.output.token_budget:
            return RenderedDoc(text, level, estimated, False)
    text = _render(project, run_result, version=version, level=2)
    return RenderedDoc(text, 2, estimate_tokens(text), True)
