from __future__ import annotations

from pathlib import Path

import pytest

from codewhy.config import Config
from codewhy.extraction.python_ast import extract_python
from codewhy.llm.chunking import Chunk
from codewhy.pipeline import run_pipeline
from codewhy.project import build_project
from codewhy.summarize.prompts import (
    NO_ENTRY_POINT_INSTRUCTION,
    PROMPT_VERSION,
    chunk_prompt,
    combine_prompt,
    file_prompt,
    module_prompt,
    project_prompt,
    render_structure,
    retry_suffix,
)
from codewhy.summarize.schemas import FilePass, ModulePass, ProjectPass
from codewhy.text import first_sentence
from tests.helpers import FIXED_TIME
from tests.support.fake_provider import FakeProvider

FIXTURES = Path(__file__).parent / "fixtures"

# Copied from docs/ARCHITECTURE.md -> Prompt rules; must appear verbatim.
RULE = (
    "Do not invent behavior, APIs, or functionality not evidenced in the provided structure or source. "
    "If intent cannot be confidently established, say so plainly rather than fabricating a plausible explanation."
)

SOURCE = '''"""Order handling."""
import json


class Order(Base):
    """An order. It has lines."""

    def total(self) -> int:
        """Sum the lines."""
        return 0


def load(path: str) -> Order:
    """Read an order from disk."""
    return Order()


def helper():
    pass
'''


def structure():
    return extract_python("shop/orders.py", SOURCE)


def every_prompt() -> dict[str, str]:
    fs = structure()
    chunk = Chunk(1, 3, "import json\n")
    return {
        "file": file_prompt(fs, ["shop/db.py"], ["main.py"], include_source=True),
        "chunk": chunk_prompt(fs, chunk, ["Order", "load"], ["shop/db.py"], ["main.py"]),
        "combine": combine_prompt(fs, ["Part one.", "Part two."], ["shop/db.py"], ["main.py"]),
        "module": module_prompt("shop", [("shop/orders.py", "Handles orders.")], "main.py -> shop/orders.py", ["json"]),
        "project": project_prompt("demo", None, [(".", "Root."), ("shop", "Orders.")], ". -> shop", ["README.md"]),
    }


def test_prompt_version_exists() -> None:
    assert isinstance(PROMPT_VERSION, int) and PROMPT_VERSION >= 1


@pytest.mark.parametrize("name", ["file", "chunk", "combine", "module", "project"])
def test_every_prompt_has_the_rule_verbatim_and_sections_in_order(name: str) -> None:
    prompt = every_prompt()[name]
    assert RULE in prompt
    order = [
        prompt.index("careful software engineer"),
        prompt.index(RULE),
        prompt.index("Reply with a JSON object only"),
        prompt.index("Task:"),
        prompt.index("Inputs:"),
    ]
    assert order == sorted(order)
    assert "no headings" in prompt and "no code fences" in prompt
    assert "new to it" in prompt and "paste" in prompt


@pytest.mark.parametrize(
    ("name", "model"),
    [("file", FilePass), ("chunk", FilePass), ("combine", FilePass), ("module", ModulePass), ("project", ProjectPass)],
)
def test_schema_field_descriptions_travel_with_the_prompt(name: str, model) -> None:
    prompt = every_prompt()[name]
    for field_name, field in model.model_fields.items():
        assert f'"{field_name}"' in prompt
        assert field.description in prompt


def test_file_prompt_has_paths_structure_and_source_when_asked() -> None:
    fs = structure()
    prompt = file_prompt(fs, ["shop/db.py"], ["main.py", "shop/api.py"], include_source=True)
    for path in ("shop/orders.py", "shop/db.py", "main.py", "shop/api.py"):
        assert path in prompt
    assert "why this file exists" in prompt
    assert "def load(path: str) -> Order" in prompt
    assert "## Full source" in prompt and "return Order()" in prompt
    without = file_prompt(fs, ["shop/db.py"], ["main.py"], include_source=False)
    assert "## Full source" not in without and "return Order()" not in without


def test_file_prompt_with_no_neighbors_says_none() -> None:
    prompt = file_prompt(structure(), [], [], include_source=False)
    assert "## Project files this file imports\n(none)" in prompt
    assert "## Project files that import this file\n(none)" in prompt


def test_file_prompts_never_contain_neighbor_summaries() -> None:
    project = build_project(FIXTURES / "flat_scripts", Config())

    def respond(call):
        if "## Full source" in call.prompt:
            path = call.prompt.split("path: ", 1)[1].split("\n", 1)[0]
            return {"purpose": f"UNIQUE-PURPOSE-OF-{path}."}
        return None

    # Module and project calls get plain text for whatever fields their schema requires.
    fake = FakeProvider(respond=lambda call: respond(call) or {k: "text." for k in call.schema["required"]})
    run_pipeline(project, fake, Config(), clock=lambda: FIXED_TIME, use_cache=False)
    file_prompts = [call.prompt for call in fake.calls if "## Full source" in call.prompt]
    assert len(file_prompts) == 3
    for prompt in file_prompts:
        assert "UNIQUE-PURPOSE-OF-" not in prompt
    module_prompt_text = next(call.prompt for call in fake.calls if "Files in this module" in call.prompt)
    assert "UNIQUE-PURPOSE-OF-solver.py." in module_prompt_text


def test_fence_outgrows_backticks_in_the_source() -> None:
    fs = extract_python("doc.py", 'TEXT = """\n````\ncode\n````\n"""\n')
    prompt = file_prompt(fs, [], [], include_source=True)
    assert "`````\nTEXT" in prompt


def test_render_structure_is_source_ordered_and_deterministic() -> None:
    rendered = render_structure(structure())
    assert rendered == render_structure(structure())
    lines = rendered.split("\n")
    assert lines == [
        "class Order(Base) — An order.",
        "  def total(self) -> int — Sum the lines.",
        "def load(path: str) -> Order — Read an order from disk.",
        "def helper()",
    ]


def test_render_structure_of_a_file_without_definitions() -> None:
    assert render_structure(extract_python("c.py", "VALUE = 1\n")) == "(none)"


def test_project_prompt_without_entry_point_tells_the_model_not_to_guess() -> None:
    prompt = project_prompt("demo", None, [(".", "Root.")], "", [])
    assert NO_ENTRY_POINT_INSTRUCTION in prompt
    assert "no conventional entry point was detected" in prompt.lower()
    assert "do not guess or infer one" in prompt


def test_project_prompt_with_entry_point_names_it() -> None:
    prompt = project_prompt("demo", "src/app/cli.py", [(".", "Root.")], "", [])
    assert "Entry point: src/app/cli.py" in prompt
    assert NO_ENTRY_POINT_INSTRUCTION not in prompt


def test_project_prompt_caps_other_files_at_50() -> None:
    others = [f"assets/file_{n:03}.png" for n in range(53)]
    prompt = project_prompt("demo", None, [(".", "Root.")], "", others)
    assert "assets/file_049.png" in prompt
    assert "assets/file_050.png" not in prompt
    assert "… and 3 more" in prompt


def test_module_and_project_prompts_show_omitted_counts() -> None:
    assert "4 more files omitted from this prompt" in module_prompt("m", [("m/a.py", "A.")], "", [], omitted=4)
    assert "2 more modules omitted from this prompt" in project_prompt("p", None, [(".", "R.")], "", [], omitted=2)
    assert "omitted" not in module_prompt("m", [("m/a.py", "A.")], "", [])


def test_retry_suffix_format() -> None:
    assert retry_suffix("purpose: Field required") == (
        "\n\nYour previous response was invalid: purpose: Field required. "
        "Reply with only a JSON object matching the schema."
    )


# --- text.first_sentence ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Parses input. Then writes output.", "Parses input."),
        ("  Does   a\n thing!  More here.", "Does a thing!"),
        ("Is it? Yes.", "Is it?"),
        ("No terminal punctuation", "No terminal punctuation"),
        ("Version 2.5 of the parser. Second.", "Version 2.5 of the parser."),
        ("", ""),
        ("   ", ""),
    ],
)
def test_first_sentence(text: str, expected: str) -> None:
    assert first_sentence(text) == expected


def test_first_sentence_is_cut_with_an_ellipsis() -> None:
    result = first_sentence("x" * 400)
    assert len(result) == 300 and result.endswith("…")
    assert first_sentence("abcdefghij", max_chars=5) == "abcd…"
