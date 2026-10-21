"""The budget invariant (docs/ARCHITECTURE.md -> Context budgeting).

Every prompt any pass sends satisfies `estimate_tokens(prompt) <= prompt_budget`,
on every fixture and at every context size, including retry prompts. A budget
too small to work with yields "context too small" failures, never a prompt
over budget.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codewhy.config import Config
from codewhy.llm.budget import prompt_budget
from codewhy.llm.provider import MalformedOutput
from codewhy.pipeline import run_pipeline
from codewhy.project import build_project
from tests.fixtures.builders import build_oversized_file
from tests.helpers import FIXED_TIME, make_project
from tests.support.fake_provider import BudgetCheckingFakeProvider, Call, FakeProvider, synthesize

FIXTURES = Path(__file__).parent / "fixtures"
COMMITTED = ["flat_scripts", "nested_modules", "python_js_mixed"]
# (context_tokens, max_output_tokens); the last two are deliberately tiny.
SIZES = [(16384, 1024), (8192, 1024), (4096, 512), (2400, 256), (1500, 128), (1200, 100), (600, 50)]
TINY = (600, 50)


def build_wide_module(tmp_path: Path) -> Path:
    """40 files in one folder importing each other: module prompts reach ladder step 3 and edge cutting."""
    files = {}
    for n in range(40):
        imports = "".join(f"from shop.part_{m:02} import step_{m:02}\n" for m in range(n + 1, min(n + 6, 40)))
        files[f"shop/part_{n:02}.py"] = f"{imports}\n\ndef step_{n:02}(value: int) -> int:\n    return value + {n}\n"
    files["main.py"] = "from shop.part_00 import step_00\n\nprint(step_00(1))\n"
    return make_project(tmp_path / "wide_module", files)


def project_for(name: str, tmp_path: Path):
    if name == "oversized_file":
        return build_project(build_oversized_file(tmp_path, chunk_tokens=600), Config())
    if name == "wide_module":
        return build_project(build_wide_module(tmp_path), Config())
    return build_project(FIXTURES / name, Config())


def malformed_first(call: Call) -> dict | Exception:
    """Every first attempt is malformed, so every prompt is also sent as a retry."""
    if "Your previous response was invalid" not in call.prompt:
        return MalformedOutput("the reply did not contain a JSON object " + "x" * 2000)
    return synthesize(call.schema, call.prompt)


def verbose(call: Call) -> dict:
    """Near the schema's length caps, so module and project prompts climb the overflow ladder."""
    detail = "It also " + "handles one more detail of the workflow " * 25 + "."
    return {name: f"Short opener for {name}. {detail}"[:1150] for name in call.schema["required"]}


MODES = {"first-try": None, "with-retries": malformed_first, "verbose-model": verbose}


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize("size", SIZES, ids=[f"ctx{c}-out{o}" for c, o in SIZES])
@pytest.mark.parametrize("fixture", [*COMMITTED, "oversized_file", "wide_module"])
def test_every_prompt_fits_the_budget(fixture: str, size: tuple[int, int], mode: str, tmp_path: Path) -> None:
    context, max_out = size
    config = Config.model_validate({"llm": {"context_tokens": context, "max_output_tokens": max_out}})
    budget = prompt_budget(context, max_out)
    inner = FakeProvider(respond=MODES[mode])
    checked = BudgetCheckingFakeProvider(inner, budget)  # fails the test on any over-budget prompt
    project = project_for(fixture, tmp_path)

    result = run_pipeline(project, checked, config, clock=lambda: FIXED_TIME)

    every = [*result.files.values(), *result.modules.values(), result.project]
    for item in every:
        assert item.failure is None or "context too small" in item.failure, item.failure
    if size == TINY:
        assert inner.calls == []
        assert all(r.failure is not None or r.data.purpose == "Empty file." for r in result.files.values())
        assert all(r.failure is not None for r in [*result.modules.values(), result.project])
    if size == SIZES[0]:
        assert all(item.failure is None for item in every)


def test_wide_module_climbs_to_step_3_and_cuts_edges(tmp_path: Path) -> None:
    config = Config.model_validate({"llm": {"context_tokens": 2400, "max_output_tokens": 256}})
    inner = FakeProvider(respond=verbose)
    run_pipeline(project_for("wide_module", tmp_path), BudgetCheckingFakeProvider(inner, prompt_budget(2400, 256)), config, clock=lambda: FIXED_TIME)
    (module_prompt,) = [c.prompt for c in inner.calls if "## Module\npath: shop\n" in c.prompt]
    assert "more files omitted from this prompt" in module_prompt
    assert "more edges" in module_prompt


def test_oversized_file_chunks_at_a_moderate_context(tmp_path: Path) -> None:
    config = Config.model_validate({"llm": {"context_tokens": 2400, "max_output_tokens": 256}})
    project = project_for("oversized_file", tmp_path)
    inner = FakeProvider()
    result = run_pipeline(project, BudgetCheckingFakeProvider(inner, prompt_budget(2400, 256)), config, clock=lambda: FIXED_TIME)
    assert result.files["inventory/report.py"].failure is None
    assert sum("## This part: lines" in c.prompt for c in inner.calls) > 3
    assert sum("## Part summaries" in c.prompt for c in inner.calls) == 1
