"""docs/TESTS.md -> Layer 3 — Cache: one named test per scenario row, with call counting.

Every test scans the same small project (a root module and a `pkg` module) with a
scripted FakeProvider whose answers depend only on the subject, never on the
source, so "the model returned identical text after an edit" is under test
control. `passes(fake)` names the (pass, subject) of every provider call.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from codewhy.cache import keys
from codewhy.cache.db import CacheDB, CacheIOError, CacheLocked
from codewhy.config import Config
from codewhy.llm.provider import TransientError
from codewhy.pipeline import RunResult, run_pipeline
from codewhy.project import build_project
from codewhy.render.docs_tree import render_docs_tree
from codewhy.render.single_file import render_single_file
from tests.helpers import FIXED_TIME, make_project
from tests.support.fake_provider import Call, FakeProvider

PROJECT = {
    "main.py": "from pkg.a import run\n\nrun()\n",
    "pkg/__init__.py": "NAME = \"pkg\"\n",
    "pkg/a.py": "from pkg.b import helper\n\n\ndef run():\n    return helper()\n",
    "pkg/b.py": "def helper():\n    return 1\n",
    "pkg/c.py": "def other():\n    return 2\n",
}
FILES = sorted(PROJECT)
MODULES = [".", "pkg"]
EVERYTHING = {("file", path) for path in FILES} | {("module", m) for m in MODULES} | {("project", "")}


class Model:
    """Scripted answers keyed by subject; override `purpose`, `notes`, `responsibility` per test."""

    def __init__(self) -> None:
        self.purpose: dict[str, str] = {}
        self.notes: dict[str, str] = {}
        self.responsibility: dict[str, str] = {}

    def respond(self, call: Call) -> dict:
        kind, subject = classify(call)
        if kind == "project":
            return {"overview": "Overview.", "architecture": "Architecture."}
        if kind == "module":
            return {
                "responsibility": self.responsibility.get(subject, f"Responsibility of {subject}."),
                "how_pieces_cooperate": "They cooperate.",
            }
        return {"purpose": self.purpose.get(subject, f"Purpose of {subject}."), "notes": self.notes.get(subject)}

    def provider(self, **kwargs) -> FakeProvider:
        return FakeProvider(respond=self.respond, **kwargs)


def classify(call: Call) -> tuple[str, str]:
    if "## Project\n" in call.prompt:
        return "project", ""
    if "## Module\npath: " in call.prompt:
        return "module", call.prompt.split("## Module\npath: ", 1)[1].split("\n", 1)[0]
    return "file", call.prompt.split("## File\npath: ", 1)[1].split("\n", 1)[0]


def passes(fake: FakeProvider) -> set[tuple[str, str]]:
    return {classify(call) for call in fake.calls}


def scan(root: Path, provider: FakeProvider, config: Config | None = None, *, full: bool = False, clock=lambda: FIXED_TIME) -> RunResult:
    config = config or Config()
    return run_pipeline(build_project(root, config), provider, config, clock=clock, full=full)


def rows(root: Path) -> dict[tuple[str, str], tuple[str, str]]:
    """(pass, subject) -> (input_key, generated_at) straight from the DB."""
    connection = sqlite3.connect(root / ".codewhy" / "cache.db")
    try:
        found = connection.execute("SELECT pass, subject, input_key, generated_at FROM pass_cache").fetchall()
    finally:
        connection.close()
    return {(kind, subject): (key, at) for kind, subject, key, at in found}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return make_project(tmp_path, PROJECT)


@pytest.fixture
def model() -> Model:
    return Model()


def primed(root: Path, model: Model) -> None:
    first = model.provider()
    scan(root, first)
    assert passes(first) == EVERYTHING and len(first.calls) == len(EVERYTHING)


def edit(root: Path, path: str, text: str) -> None:
    make_project(root, {path: text})


def test_first_ever_run_generates_everything_and_creates_the_db(root: Path, model: Model) -> None:
    assert not (root / ".codewhy").exists()
    fake = model.provider()
    result = scan(root, fake)
    assert passes(fake) == EVERYTHING
    assert result.counts == {"generated": len(EVERYTHING), "cached": 0, "failed": 0, "skipped": 0}
    assert set(rows(root)) == EVERYTHING
    assert (root / ".codewhy" / ".gitignore").read_text(encoding="utf-8") == "*\n"


def test_no_files_changed_zero_calls_and_identical_output(root: Path, model: Model) -> None:
    config = Config()
    first_fake = model.provider()
    project = build_project(root, config)
    first = run_pipeline(project, first_fake, config, clock=lambda: FIXED_TIME)
    later = FIXED_TIME + timedelta(days=1)
    second_fake = model.provider()
    project = build_project(root, config)
    second = run_pipeline(project, second_fake, config, clock=lambda: later)
    assert second_fake.calls == []
    assert second.counts == {"generated": 0, "cached": len(EVERYTHING), "failed": 0, "skipped": 0}
    assert render_single_file(project, second, config, version="0").text == render_single_file(project, first, config, version="0").text
    assert render_docs_tree(project, second, config, version="0") == render_docs_tree(project, first, config, version="0")


def test_source_edited_identical_purpose_reruns_only_the_file(root: Path, model: Model) -> None:
    primed(root, model)
    edit(root, "pkg/b.py", "def helper():\n    # same behaviour, new comment\n    return 1\n")
    fake = model.provider()
    scan(root, fake)
    assert passes(fake) == {("file", "pkg/b.py")}


def test_purpose_differs_reruns_module_but_not_project_when_responsibility_is_identical(root: Path, model: Model) -> None:
    primed(root, model)
    edit(root, "pkg/b.py", "def helper():\n    return 2\n")
    model.purpose["pkg/b.py"] = "A different purpose."
    fake = model.provider()
    scan(root, fake)
    assert passes(fake) == {("file", "pkg/b.py"), ("module", "pkg")}


def test_purpose_differs_and_responsibility_differs_reruns_project(root: Path, model: Model) -> None:
    primed(root, model)
    edit(root, "pkg/b.py", "def helper():\n    return 2\n")
    model.purpose["pkg/b.py"] = "A different purpose."
    model.responsibility["pkg"] = "A different responsibility."
    fake = model.provider()
    scan(root, fake)
    assert passes(fake) == {("file", "pkg/b.py"), ("module", "pkg"), ("project", "")}


def test_only_notes_change_does_not_rerun_the_module(root: Path, model: Model) -> None:
    primed(root, model)
    edit(root, "pkg/b.py", "def helper():\n    return 1  # quirk\n")
    model.notes["pkg/b.py"] = "A new caveat."
    fake = model.provider()
    result = scan(root, fake)
    assert passes(fake) == {("file", "pkg/b.py")}
    assert result.files["pkg/b.py"].data.notes == "A new caveat."


def test_import_edge_change_reruns_affected_files_module_and_project(root: Path, model: Model) -> None:
    primed(root, model)
    # a -> b becomes a -> c; every scripted text stays identical.
    edit(root, "pkg/a.py", "from pkg.c import other\n\n\ndef run():\n    return other()\n")
    fake = model.provider()
    scan(root, fake)
    assert passes(fake) == {
        ("file", "pkg/a.py"),  # source and imports
        ("file", "pkg/b.py"),  # imported_by lost a
        ("file", "pkg/c.py"),  # imported_by gained a
        ("module", "pkg"),  # relevant edges
        ("project", ""),  # project edge list
    }


def test_new_file_added_to_a_module_reruns_that_module(root: Path, model: Model) -> None:
    primed(root, model)
    edit(root, "pkg/d.py", "VALUE = 4\n")
    fake = model.provider()
    scan(root, fake)
    assert passes(fake) == {("file", "pkg/d.py"), ("module", "pkg")}
    assert ("file", "pkg/d.py") in rows(root)


def test_file_deleted_removes_its_row_and_reruns_its_module(root: Path, model: Model) -> None:
    primed(root, model)
    (root / "pkg" / "c.py").unlink()
    fake = model.provider()
    scan(root, fake)
    assert passes(fake) == {("module", "pkg")}
    assert ("file", "pkg/c.py") not in rows(root)


def test_module_left_with_no_files_has_its_row_removed(root: Path, model: Model) -> None:
    edit(root, "extra/tool.py", "X = 1\n")
    scan(root, model.provider())
    assert ("module", "extra") in rows(root)
    (root / "extra" / "tool.py").unlink()
    (root / "extra").rmdir()
    scan(root, model.provider())
    assert ("module", "extra") not in rows(root)
    assert ("file", "extra/tool.py") not in rows(root)


def test_file_renamed_moves_its_row_and_changes_neighbor_keys(root: Path, model: Model) -> None:
    primed(root, model)
    (root / "pkg" / "b.py").rename(root / "pkg" / "bee.py")
    edit(root, "pkg/a.py", "from pkg.bee import helper\n\n\ndef run():\n    return helper()\n")
    fake = model.provider()
    scan(root, fake)
    current = rows(root)
    assert ("file", "pkg/b.py") not in current and ("file", "pkg/bee.py") in current
    assert passes(fake) == {("file", "pkg/bee.py"), ("file", "pkg/a.py"), ("module", "pkg"), ("project", "")}


def test_entry_point_change_reruns_only_the_project_pass(root: Path, model: Model) -> None:
    primed(root, model)
    config = Config.model_validate({"project": {"entry_point": "pkg/a.py"}})
    fake = model.provider()
    scan(root, fake, config)
    assert passes(fake) == {("project", "")}


CONFIG_FP_CHANGES = {
    "provider": "openai-compat",
    "model": "another-model",
    "context_tokens": 20000,
    "max_output_tokens": 900,
    "temperature": 0.5,
    "seed": 7,
    "reasoning_effort": "high",
    "structured_output": "json",
}


def with_llm(config: Config, **changes: object) -> Config:
    return config.model_copy(update={"llm": config.llm.model_copy(update=changes)})


@pytest.mark.parametrize("field", sorted(CONFIG_FP_CHANGES))
def test_each_config_fp_field_reruns_everything(root: Path, model: Model, field: str) -> None:
    primed(root, model)
    fake = model.provider()
    scan(root, fake, with_llm(Config(), **{field: CONFIG_FP_CHANGES[field]}))
    assert passes(fake) == EVERYTHING


def test_settings_outside_config_fp_rerun_nothing(root: Path, model: Model) -> None:
    primed(root, model)
    config = Config.model_validate({"output": {"mode": "both", "token_budget": 123}})
    fake = model.provider()
    scan(root, fake, with_llm(config, timeout_seconds=5))
    assert fake.calls == []


def test_pipeline_version_bump_reruns_everything(root: Path, model: Model, monkeypatch: pytest.MonkeyPatch) -> None:
    primed(root, model)
    monkeypatch.setattr(keys, "PIPELINE_VERSION", keys.PIPELINE_VERSION + 1)
    fake = model.provider()
    scan(root, fake)
    assert passes(fake) == EVERYTHING


def test_full_calls_every_pass_and_rewrites_rows(root: Path, model: Model) -> None:
    primed(root, model)
    later = FIXED_TIME + timedelta(hours=1)
    fake = model.provider()
    result = scan(root, fake, full=True, clock=lambda: later)
    assert passes(fake) == EVERYTHING and result.counts["cached"] == 0
    assert {at for _, at in rows(root).values()} == {"2026-01-01T13:00:00Z"}


def test_failed_result_is_not_cached_and_the_next_run_retries_it(root: Path, model: Model) -> None:
    # Call 0 is the first file pass (path order): main.py.
    first = scan(root, model.provider(errors={0: TransientError("temporarily down")}))
    assert first.files["main.py"].failure is not None and first.counts["failed"] == 1
    assert ("file", "main.py") not in rows(root)
    fake = model.provider()
    second = scan(root, fake)
    # The member went from the failed marker to real text, so its module re-runs too.
    assert passes(fake) == {("file", "main.py"), ("module", ".")}
    assert second.counts["failed"] == 0


@pytest.mark.parametrize("completed", [1, 4, len(EVERYTHING) - 1])
def test_interrupted_run_resumes_with_only_the_remaining_calls(root: Path, model: Model, completed: int) -> None:
    # A previous completed run leaves a row for a file that is then deleted;
    # the interrupted run must not clean it up.
    edit(root, "pkg/gone.py", "G = 1\n")
    scan(root, model.provider())
    (root / "pkg" / "gone.py").unlink()
    config = with_llm(Config(), seed=1)  # every key misses, so every pass must run
    interrupted = model.provider(errors={completed: KeyboardInterrupt()})
    with pytest.raises(KeyboardInterrupt):
        scan(root, interrupted, config)
    assert len(interrupted.calls) == completed + 1
    assert ("file", "pkg/gone.py") in rows(root)  # no cleanup after an interrupted run
    done = {classify(call) for call in interrupted.calls[:completed]}

    resumed = model.provider()
    scan(root, resumed, config)
    assert passes(resumed) == EVERYTHING - done
    assert len(resumed.calls) == len(EVERYTHING) - completed
    assert ("file", "pkg/gone.py") not in rows(root)


def test_garbage_db_is_treated_as_empty_and_rebuilt(root: Path, model: Model, caplog: pytest.LogCaptureFixture) -> None:
    (root / ".codewhy").mkdir()
    (root / ".codewhy" / "cache.db").write_bytes(b"this is not a database" * 100)
    fake = model.provider()
    with caplog.at_level(logging.WARNING):
        result = scan(root, fake)
    assert "cache unreadable, rebuilding" in caplog.text
    assert passes(fake) == EVERYTHING and result.counts["failed"] == 0
    assert set(rows(root)) == EVERYTHING


def test_unknown_schema_version_is_treated_as_empty_and_rebuilt(root: Path, model: Model, caplog: pytest.LogCaptureFixture) -> None:
    primed(root, model)
    connection = sqlite3.connect(root / ".codewhy" / "cache.db")
    connection.execute("UPDATE meta SET value = '999' WHERE key = 'schema_version'")
    connection.commit()
    connection.close()
    fake = model.provider()
    with caplog.at_level(logging.WARNING):
        scan(root, fake)
    assert "cache unreadable, rebuilding" in caplog.text
    assert passes(fake) == EVERYTHING
    assert set(rows(root)) == EVERYTHING


@pytest.mark.parametrize("error", [CacheIOError("disk I/O error"), CacheLocked("locked")])
def test_cache_failure_mid_run_degrades_without_losing_results(
    root: Path, model: Model, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    stores: list[str] = []
    original = CacheDB.store

    def flaky(self, pass_name, subject, input_key, result):
        stores.append(subject)
        if len(stores) == 2:
            raise error
        return original(self, pass_name, subject, input_key, result)

    monkeypatch.setattr(CacheDB, "store", flaky)
    fake = model.provider()
    with caplog.at_level(logging.WARNING):
        result = scan(root, fake)
    assert "continuing without cache" in caplog.text
    assert len(stores) == 2  # caching stopped after the failure
    assert passes(fake) == EVERYTHING
    assert result.counts == {"generated": len(EVERYTHING), "cached": 0, "failed": 0, "skipped": 0}
    assert all(r.failure is None for r in [*result.files.values(), *result.modules.values(), result.project])
