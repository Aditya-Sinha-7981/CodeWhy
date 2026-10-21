from __future__ import annotations

from codewhy.config import Config
from codewhy.llm.budget import estimate_tokens
from codewhy.project import Module
from codewhy.summarize.module_pass import (
    MODULE_CONTEXT_TOO_SMALL,
    UNAVAILABLE,
    assemble_module_prompt,
    run_module_pass,
)
from codewhy.summarize.prompts import module_prompt
from codewhy.summarize.schemas import ModulePass
from tests.helpers import FIXED_TIME
from tests.support.fake_provider import FakeProvider

LONG = " It also " + "keeps track of a great many details " * 12 + "."


def make_module(count: int) -> tuple[Module, dict[str, str], dict[str, int]]:
    files = [f"pkg/file_{n:02}.py" for n in range(count)]
    purposes = {path: f"Opens {path}.{LONG}" for path in files}
    # In-degree rises with the index, so in-degree order is the reverse of path order.
    in_degree = {path: n for n, path in enumerate(files)}
    return Module("pkg", files), purposes, in_degree


def budget_for(module: Module, items, edges: str = "", omitted: int = 0) -> int:
    return estimate_tokens(module_prompt(module.path, items, edges, ["requests"], omitted=omitted))


def test_step_1_full_purposes_when_they_fit() -> None:
    module, purposes, degree = make_module(3)
    full = sorted(purposes.items())
    assembled = assemble_module_prompt(module, purposes, "", ["requests"], degree, budget=budget_for(module, full))
    assert assembled.failure is None
    assert LONG.strip() in assembled.prompt
    assert assembled.inputs == {
        "module": "pkg",
        "files": [list(item) for item in full],
        "omitted": 0,
        "edges": "",
        "external": ["requests"],
    }


def test_step_2_first_sentences_when_full_text_overflows() -> None:
    module, purposes, degree = make_module(3)
    short = [(path, f"Opens {path}.") for path in sorted(purposes)]
    budget = budget_for(module, short)
    assert budget < budget_for(module, sorted(purposes.items()))
    assembled = assemble_module_prompt(module, purposes, "", ["requests"], degree, budget=budget)
    assert "keeps track" not in assembled.prompt
    assert "- pkg/file_00.py: Opens pkg/file_00.py." in assembled.prompt
    assert "omitted" not in assembled.prompt
    assert assembled.inputs["files"] == [list(item) for item in short]
    assert estimate_tokens(assembled.prompt) <= budget


def test_step_3_keeps_highest_in_degree_files_and_counts_the_rest() -> None:
    module, purposes, degree = make_module(10)
    # Room for exactly four first-sentence entries plus the omitted line.
    kept = [(f"pkg/file_{n:02}.py", f"Opens pkg/file_{n:02}.py.") for n in (6, 7, 8, 9)]
    budget = budget_for(module, kept, omitted=6)
    assembled = assemble_module_prompt(module, purposes, "", ["requests"], degree, budget=budget)
    assert assembled.inputs["files"] == [list(item) for item in kept]
    assert assembled.inputs["omitted"] == 6
    assert "6 more files omitted from this prompt" in assembled.prompt
    assert "pkg/file_05.py" not in assembled.prompt
    assert estimate_tokens(assembled.prompt) <= budget


def test_step_3_ties_in_in_degree_are_broken_by_path() -> None:
    module, purposes, _ = make_module(4)
    degree = {path: 1 for path in module.files}
    kept = [(f"pkg/file_{n:02}.py", f"Opens pkg/file_{n:02}.py.") for n in (0, 1)]
    assembled = assemble_module_prompt(module, purposes, "", ["requests"], degree, budget=budget_for(module, kept, omitted=2))
    assert assembled.inputs["files"] == [list(item) for item in kept]


def test_edges_are_cut_line_by_line_when_even_no_files_do_not_fit() -> None:
    module, purposes, degree = make_module(2)
    edges = "\n".join(f"pkg/file_00.py -> other/target_{n:03}.py" for n in range(200))
    kept_edges = "\n".join(edges.split("\n")[:5]) + "\n… and 195 more edges"
    budget = budget_for(module, [], kept_edges, omitted=2)
    assembled = assemble_module_prompt(module, purposes, edges, ["requests"], degree, budget=budget)
    assert assembled.inputs["edges"] == kept_edges
    assert assembled.inputs["files"] == []
    assert assembled.inputs["omitted"] == 2
    assert "… and 195 more edges" in assembled.prompt


def test_nothing_fits_is_a_context_too_small_failure_without_a_call() -> None:
    module, purposes, degree = make_module(2)
    config = Config.model_validate({"llm": {"context_tokens": 600, "max_output_tokens": 50}})
    fake = FakeProvider()
    run = run_module_pass(module, purposes, "", ["requests"], degree, provider=fake, config=config, clock=lambda: FIXED_TIME)
    assert run.result.failure == MODULE_CONTEXT_TOO_SMALL
    assert run.result.data is None
    assert fake.calls == []


def test_failed_member_files_show_the_placeholder() -> None:
    module = Module("pkg", ["pkg/a.py", "pkg/b.py"])
    fake = FakeProvider()
    run = run_module_pass(
        module, {"pkg/a.py": "Reads input."}, "", [], {}, provider=fake, config=Config(), clock=lambda: FIXED_TIME
    )
    assert run.result.failure is None
    assert isinstance(run.result.data, ModulePass)
    assert "- pkg/b.py: (summary unavailable)" in fake.calls[0].prompt
    assert run.prompt_inputs["files"] == [["pkg/a.py", "Reads input."], ["pkg/b.py", UNAVAILABLE]]
    assert run.result.metadata == {} and run.result.generated_at == FIXED_TIME
