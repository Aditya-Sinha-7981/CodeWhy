from __future__ import annotations

from codewhy.config import Config
from codewhy.llm.budget import estimate_tokens
from codewhy.summarize.project_pass import (
    PROJECT_CONTEXT_TOO_SMALL,
    assemble_project_prompt,
    module_edges_text,
    run_project_pass,
)
from codewhy.summarize.prompts import NO_ENTRY_POINT_INSTRUCTION, project_prompt
from codewhy.summarize.schemas import ProjectPass
from tests.helpers import FIXED_TIME
from tests.support.fake_provider import FakeProvider

LONG = " It also " + "coordinates many moving parts " * 15 + "."


def modules(count: int) -> tuple[dict[str, str], dict[str, int]]:
    paths = [f"pkg/mod_{n:02}" for n in range(count)]
    texts = {path: f"Handles {path}.{LONG}" for path in paths}
    degree = {path: n for n, path in enumerate(paths)}
    return texts, degree


def budget_for(items, edges: str = "", others=(), omitted: int = 0, entry: str | None = None) -> int:
    return estimate_tokens(project_prompt("demo", entry, items, edges, list(others), omitted=omitted))


def assemble(texts, degree, budget, edges="", others=(), entry=None):
    return assemble_project_prompt("demo", entry, texts, degree, edges, list(others), budget=budget)


def test_module_edges_are_between_modules_deduplicated_and_sorted() -> None:
    edges = [
        ("pkg/b/x.py", "pkg/a/y.py"),
        ("pkg/b/z.py", "pkg/a/y.py"),  # same module pair again
        ("pkg/a/y.py", "pkg/a/w.py"),  # inside one module: not a module edge
        ("main.py", "pkg/b/x.py"),
    ]
    assert module_edges_text(edges) == ". -> pkg/b\npkg/b -> pkg/a"
    assert module_edges_text([]) == ""


def test_step_1_and_inputs() -> None:
    texts, degree = modules(2)
    items = sorted(texts.items())
    assembled = assemble(texts, degree, budget_for(items, ". -> pkg"), ". -> pkg", ["README.md"], "main.py")
    assert assembled.failure is None
    assert "Entry point: main.py" in assembled.prompt
    assert assembled.inputs == {
        "root_name": "demo",
        "entry_point": "main.py",
        "modules": [list(item) for item in items],
        "omitted": 0,
        "module_edges": ". -> pkg",
        "other_files": ["README.md"],
    }


def test_step_2_first_sentences() -> None:
    texts, degree = modules(3)
    short = [(path, f"Handles {path}.") for path in sorted(texts)]
    assembled = assemble(texts, degree, budget_for(short))
    assert "coordinates" not in assembled.prompt
    assert assembled.inputs["modules"] == [list(item) for item in short]
    assert assembled.inputs["omitted"] == 0


def test_step_3_orders_by_module_in_degree_and_counts_omitted() -> None:
    texts, degree = modules(8)
    kept = [(f"pkg/mod_{n:02}", f"Handles pkg/mod_{n:02}.") for n in (5, 6, 7)]
    budget = budget_for(kept, omitted=5)
    assembled = assemble(texts, degree, budget)
    assert assembled.inputs["modules"] == [list(item) for item in kept]
    assert assembled.inputs["omitted"] == 5
    assert "5 more modules omitted from this prompt" in assembled.prompt
    assert estimate_tokens(assembled.prompt) <= budget


def test_no_entry_point_instruction_survives_the_ladder() -> None:
    texts, degree = modules(8)
    kept = [(f"pkg/mod_{n:02}", f"Handles pkg/mod_{n:02}.") for n in (5, 6, 7)]
    assembled = assemble(texts, degree, budget_for(kept, omitted=5))
    assert NO_ENTRY_POINT_INSTRUCTION in assembled.prompt
    assert assembled.inputs["entry_point"] is None


def test_other_files_in_inputs_are_the_capped_list_sent() -> None:
    texts, degree = modules(1)
    others = [f"docs/page_{n:02}.md" for n in range(52)]
    assembled = assemble(texts, degree, 10_000, others=others)
    assert assembled.inputs["other_files"] == [*others[:50], "… and 2 more"]
    assert "docs/page_51.md" not in assembled.prompt


def test_tiny_budget_fails_without_a_call() -> None:
    texts, degree = modules(2)
    config = Config.model_validate({"llm": {"context_tokens": 600, "max_output_tokens": 50}})
    fake = FakeProvider()
    run = run_project_pass("demo", None, texts, degree, "", [], provider=fake, config=config, clock=lambda: FIXED_TIME)
    assert run.result.failure == PROJECT_CONTEXT_TOO_SMALL
    assert fake.calls == []


def test_run_project_pass_returns_a_validated_result() -> None:
    texts, degree = modules(2)
    fake = FakeProvider()
    run = run_project_pass("demo", "main.py", texts, degree, "", [], provider=fake, config=Config(), clock=lambda: FIXED_TIME)
    assert isinstance(run.result.data, ProjectPass)
    assert run.result.metadata == {}
    assert len(fake.calls) == 1
