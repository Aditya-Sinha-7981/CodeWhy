from __future__ import annotations

from pathlib import Path

from codewhy.cache.db import CacheDB
from codewhy.config import Config
from codewhy.pipeline import run_pipeline
from codewhy.project import build_project
from codewhy.llm.provider import TransientError
from tests.helpers import FIXED_TIME, make_project
from tests.support.fake_provider import FakeProvider


def scan(root: Path, provider: FakeProvider, *, full: bool = False):
    config = Config()
    project = build_project(root, config)
    return run_pipeline(project, provider, config, clock=lambda: FIXED_TIME, full=full)


def test_unchanged_run_is_cached_and_full_bypasses_reads(tmp_path: Path) -> None:
    root = make_project(tmp_path, {"main.py": "def main():\n    return 1\n"})
    first_provider = FakeProvider()
    first = scan(root, first_provider)
    assert first_provider.calls
    second_provider = FakeProvider()
    second = scan(root, second_provider)
    assert not second_provider.calls
    assert second.counts["cached"] == first.counts["generated"]
    full_provider = FakeProvider()
    full = scan(root, full_provider, full=True)
    assert len(full_provider.calls) == len(first_provider.calls)
    assert full.counts["generated"] == first.counts["generated"]


def test_failed_result_is_not_cached_and_next_run_retries(tmp_path: Path) -> None:
    root = make_project(tmp_path, {"main.py": "x = 1\n"})
    first = scan(root, FakeProvider(errors={0: TransientError("temporary")}))
    assert first.counts["failed"] == 1
    second_provider = FakeProvider()
    second = scan(root, second_provider)
    assert second_provider.calls
    assert second.counts["failed"] == 0


def test_orphan_rows_are_removed_after_completed_run(tmp_path: Path) -> None:
    root = make_project(tmp_path, {"main.py": "x = 1\n", "gone.py": "y = 2\n"})
    scan(root, FakeProvider())
    (root / "gone.py").unlink()
    scan(root, FakeProvider())
    db = CacheDB(root / ".codewhy" / "cache.db")
    assert not db.peek("file", "gone.py", "anything")
    db.close()


def test_interrupted_run_resumes_without_cleanup(tmp_path: Path) -> None:
    root = make_project(tmp_path, {"a.py": "x = 1\n", "b.py": "y = 2\n"})

    class InterruptingProvider(FakeProvider):
        def generate_json(self, prompt, schema, *, max_output_tokens, temperature=None):
            if len(self.calls) == 1:
                raise KeyboardInterrupt()
            return super().generate_json(prompt, schema, max_output_tokens=max_output_tokens, temperature=temperature)

    try:
        scan(root, InterruptingProvider())
    except KeyboardInterrupt:
        pass
    resumed_provider = FakeProvider()
    resumed = scan(root, resumed_provider)
    assert resumed_provider.calls
    assert resumed.counts["cached"] == 1
