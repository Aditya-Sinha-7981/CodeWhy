from __future__ import annotations

from pathlib import Path
import threading
import time

from codewhy.config import Config
from codewhy.pipeline import run_pipeline
from codewhy.project import build_project
from tests.helpers import FIXED_TIME
from tests.support.fake_provider import FakeProvider, synthesize

FIXTURES = Path(__file__).parent / "fixtures"


def delayed_provider(*, concurrent: bool) -> tuple[FakeProvider, list[int]]:
    lock = threading.Lock()
    active = 0
    maximum = [0]

    def respond(call):
        nonlocal active
        with lock:
            active += 1
            maximum[0] = max(maximum[0], active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return synthesize(call.schema, call.prompt)

    return FakeProvider(respond=respond, supports_concurrency=concurrent), maximum


def test_opted_in_provider_runs_independent_passes_with_two_workers() -> None:
    project = build_project(FIXTURES / "nested_modules", Config())
    start = time.perf_counter()
    provider, maximum = delayed_provider(concurrent=True)
    result = run_pipeline(project, provider, Config(), clock=lambda: FIXED_TIME)
    elapsed = time.perf_counter() - start
    assert result.counts["failed"] == 0
    # Two workers should overlap independent calls while avoiding an unbounded
    # thread count. The loose wall-clock bound avoids making CI scheduling a
    # correctness condition.
    assert maximum[0] == 2
    assert elapsed < 1.0


def test_non_opted_in_provider_remains_sequential_and_progress_is_sorted() -> None:
    project = build_project(FIXTURES / "nested_modules", Config())
    items: list[tuple[str, str]] = []
    result = run_pipeline(
        project,
        delayed_provider(concurrent=False)[0],
        Config(),
        clock=lambda: FIXED_TIME,
        on_item=lambda kind, subject, status: items.append((kind, subject)),
    )
    assert result.counts["failed"] == 0
    files = [subject for kind, subject in items if kind == "file"]
    modules = [subject for kind, subject in items if kind == "module"]
    assert files == sorted(files)
    assert modules == [module.path for module in project.modules]
    assert items[-1] == ("project", "")


def test_concurrent_results_are_deterministic() -> None:
    project = build_project(FIXTURES / "nested_modules", Config())
    first = run_pipeline(project, delayed_provider(concurrent=True)[0], Config(), clock=lambda: FIXED_TIME)
    second = run_pipeline(project, delayed_provider(concurrent=True)[0], Config(), clock=lambda: FIXED_TIME)
    assert list(first.files) == sorted(first.files)
    assert list(first.modules) == [module.path for module in project.modules]
    assert {path: result.data.model_dump() for path, result in first.files.items()} == {
        path: result.data.model_dump() for path, result in second.files.items()
    }
