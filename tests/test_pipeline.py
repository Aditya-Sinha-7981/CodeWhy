from __future__ import annotations

from pathlib import Path

import pytest

import codewhy.pipeline as pipeline
from codewhy.config import Config
from codewhy.llm.provider import TransientError
from codewhy.pipeline import PROJECT_SUBJECT, run_pipeline
from codewhy.project import build_project
from codewhy.summarize.file_pass import EMPTY_FILE_PURPOSE
from codewhy.summarize.schemas import FilePass, ModulePass, ProjectPass
from tests.helpers import FIXED_TIME, make_project
from tests.support.fake_provider import FakeProvider

FIXTURES = Path(__file__).parent / "fixtures"


def run(project, fake=None, on_item=None):
    fake = fake or FakeProvider()
    return run_pipeline(project, fake, Config(), clock=lambda: FIXED_TIME, on_item=on_item, use_cache=False), fake


@pytest.fixture(scope="module")
def nested():
    return build_project(FIXTURES / "nested_modules", Config())


def test_order_is_files_sorted_then_modules_root_first_then_project(nested) -> None:
    items: list[tuple[str, str, str]] = []
    result, _ = run(nested, on_item=lambda *item: items.append(item))
    files = [subject for kind, subject, _ in items if kind == "file"]
    modules = [subject for kind, subject, _ in items if kind == "module"]
    assert [kind for kind, _, _ in items] == ["file"] * len(files) + ["module"] * len(modules) + ["project"]
    assert files == sorted(nested.structures)
    assert modules == [m.path for m in nested.modules]
    assert modules == sorted(modules)
    assert items[-1] == ("project", PROJECT_SUBJECT, "generated")
    assert {status for _, _, status in items} == {"generated"}
    assert list(result.files) == files and list(result.modules) == modules


def test_root_module_comes_first_even_when_a_folder_sorts_before_it(tmp_path: Path) -> None:
    root = make_project(tmp_path, {"+tools/build.py": "def build():\n    return 1\n", "main.py": "x = 1\n"})
    items: list[tuple[str, str, str]] = []
    run(build_project(root, Config()), on_item=lambda *item: items.append(item))
    assert sorted(["+tools", "."]) == ["+tools", "."]
    assert [subject for kind, subject, _ in items if kind == "module"] == [".", "+tools"]


def test_one_call_per_file_module_and_project(nested) -> None:
    result, fake = run(nested)
    assert len(fake.calls) == len(nested.structures) + len(nested.modules) + 1
    assert result.counts == {"generated": len(fake.calls), "cached": 0, "failed": 0, "skipped": 0}
    assert all(isinstance(r.data, FilePass) for r in result.files.values())
    assert all(isinstance(r.data, ModulePass) for r in result.modules.values())
    assert isinstance(result.project.data, ProjectPass)


def test_every_pass_goes_through_run_pass(nested, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, object]] = []
    original = pipeline.run_pass

    def spy(pass_name, subject, inputs, fn):
        seen.append((pass_name, subject, inputs))
        return original(pass_name, subject, inputs, fn)

    monkeypatch.setattr(pipeline, "run_pass", spy)
    result, fake = run(nested)
    assert len(seen) == len(result.files) + len(result.modules) + 1 == len(fake.calls)
    file_inputs = {subject: inputs for kind, subject, inputs in seen if kind == "file"}
    path = "src/app/core.py"
    assert file_inputs[path] == {
        "path": path,
        "source_text": nested.structures[path].source_text,
        "imports": sorted(nested.graph.successors(path)),
        "imported_by": sorted(nested.graph.predecessors(path)),
    }
    module_inputs = {subject: inputs for kind, subject, inputs in seen if kind == "module"}
    assert set(module_inputs["src/app"]) == {"module", "files", "omitted", "edges", "external"}
    project_inputs = seen[-1][2]
    assert project_inputs["entry_point"] == nested.entry_point


def test_run_pass_seam_controls_the_result(nested, monkeypatch: pytest.MonkeyPatch) -> None:
    from codewhy.summarize.result import PassResult

    canned = PassResult.ok(FilePass(purpose="From the seam."), FIXED_TIME)

    def seam(pass_name, subject, inputs, fn):
        return canned if pass_name == "file" else fn()

    monkeypatch.setattr(pipeline, "run_pass", seam)
    result, fake = run(nested)
    assert all(r is canned for r in result.files.values())
    assert len(fake.calls) == len(nested.modules) + 1
    assert "From the seam." in fake.calls[0].prompt


def test_skipped_files_are_counted_not_summarized_and_shown_as_unavailable(tmp_path: Path) -> None:
    root = make_project(
        tmp_path,
        {
            "main.py": "from helpers import load\n\n\ndef run():\n    return load()\n",
            "helpers.py": "def load():\n    return 1\n",
            "broken.py": "def broken(:\n    pass\n",
            "pkg/__init__.py": "",
            "pkg/tool.py": "VALUE = 2\n",
        },
    )
    project = build_project(root, Config())
    assert project.structures["broken.py"].status == "skipped"
    result, fake = run(project)
    assert "broken.py" not in result.files
    assert result.counts["skipped"] == 1
    assert result.files["pkg/__init__.py"].data == FilePass(purpose=EMPTY_FILE_PURPOSE)
    # 3 non-empty files + 2 modules + project; the empty __init__.py needs no call.
    assert len(fake.calls) == 3 + 2 + 1
    root_module_prompt = next(c.prompt for c in fake.calls if "## Module\npath: .\n" in c.prompt)
    assert "- broken.py: (summary unavailable)" in root_module_prompt
    assert f"- pkg/__init__.py: {EMPTY_FILE_PURPOSE}" in next(c.prompt for c in fake.calls if "path: pkg\n" in c.prompt)


def test_failed_file_and_module_become_placeholders_downstream() -> None:
    project = build_project(FIXTURES / "flat_scripts", Config())
    # Calls: main.py, solver.py, utils.py, module ".", project.
    fake = FakeProvider(errors={0: TransientError("down"), 3: TransientError("down")})
    result, _ = run(project, fake)
    assert result.files["main.py"].failure.startswith("provider unavailable")
    assert result.modules["."].failure is not None
    assert result.project.failure is None
    module_prompt = fake.calls[3].prompt
    assert "- main.py: (summary unavailable)" in module_prompt
    assert "- .: (summary unavailable)" in fake.calls[4].prompt
    assert result.counts == {"generated": 3, "cached": 0, "failed": 2, "skipped": 0}


def test_on_item_reports_failures() -> None:
    project = build_project(FIXTURES / "flat_scripts", Config())
    items: list[tuple[str, str, str]] = []
    run(project, FakeProvider(errors={1: TransientError("down")}), on_item=lambda *i: items.append(i))
    assert items[1] == ("file", "solver.py", "failed")


def test_a_failed_project_pass_is_a_result_not_an_exception() -> None:
    project = build_project(FIXTURES / "flat_scripts", Config())
    result, _ = run(project, FakeProvider(errors={4: TransientError("down")}))
    assert result.project.data is None and result.project.failure is not None


def test_layer5_placeholder_metadata_is_empty_everywhere(nested) -> None:
    result, _ = run(nested)
    every = [*result.files.values(), *result.modules.values(), result.project]
    assert all(r.metadata == {} for r in every)
    assert all(r.generated_at == FIXED_TIME for r in every)


def test_runs_are_deterministic(nested) -> None:
    first, fake_one = run(nested)
    second, fake_two = run(nested)
    assert [c.prompt for c in fake_one.calls] == [c.prompt for c in fake_two.calls]

    def dump(result):
        return (
            {k: (v.data.model_dump(), v.generated_at) for k, v in result.files.items()},
            {k: (v.data.model_dump(), v.generated_at) for k, v in result.modules.items()},
            result.project.data.model_dump(),
        )

    assert dump(first) == dump(second)
