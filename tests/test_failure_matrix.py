"""The failure-boundary matrix (docs/ARCHITECTURE.md -> Failure boundaries; task 08 Requirement 4).

One case per row. Every case asserts (a) the exit code, (b) the user-visible
message, (c) what was and was not written, (d) whether the run continued to
write documentation, and (e) the number of provider (model) calls. Provider
rows go through the real Ollama/OpenAI-compatible code behind an
httpx.MockTransport, so messages and retry counts are the real ones; the other
rows use FakeProvider.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

import codewhy.cli as cli
import codewhy.extraction.ts_treesitter as ts_treesitter
import codewhy.llm.provider as provider_module
from tests.fixtures.builders import build_oversized_file
from tests.helpers import make_project, run_cli, snapshot_tree
from tests.support.fake_provider import FakeProvider

HEALTHY = {"main.py": "from lib import go\n\ngo()\n", "lib.py": "def go():\n    return 1\n"}
MODEL = "qwen2.5-coder:14b"
FILE_ANSWER = {"purpose": "Purpose.", "notes": ""}


@dataclass
class Case:
    root: Path
    args: list[str] = field(default_factory=list)
    calls: Callable[[], int] = lambda: 0
    before: dict | None = None  # tree snapshot, for "nothing written"


@dataclass(frozen=True)
class Expect:
    exit_code: int
    stdout: tuple[str, ...] = ()
    stderr: tuple[str, ...] = ()
    documented: bool = True  # (d) the run went on to write PROJECT_DOCS.md
    unchanged: bool = False  # (c) the tree is byte-identical afterwards
    calls: int | None = None  # (e) provider calls; None = "at least one"
    check: Callable[[Path, str], None] | None = None


def fake_case(monkeypatch: pytest.MonkeyPatch, root: Path, fake: FakeProvider | None = None, args: list[str] | None = None) -> Case:
    fake = fake or FakeProvider()
    monkeypatch.setattr(cli, "build_provider", lambda config: fake)
    return Case(root, args or [], calls=lambda: len(fake.calls))


def http_case(monkeypatch: pytest.MonkeyPatch, root: Path, handler: Callable[[httpx.Request], httpx.Response], args: list[str] | None = None) -> Case:
    seen: list[httpx.Request] = []

    def logged(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    real = provider_module.build_provider
    monkeypatch.setattr(
        cli,
        "build_provider",
        lambda config: real(config, sleep=lambda seconds: None, client=httpx.Client(transport=httpx.MockTransport(logged))),
    )
    generation = ("/api/chat", "/chat/completions")
    return Case(root, args or [], calls=lambda: sum(1 for r in seen if r.url.path.endswith(generation)))


def ollama(*, reachable: bool = True, models: tuple[str, ...] = (MODEL,), chat: Callable[[httpx.Request], httpx.Response] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if not reachable:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.34.4"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in models]})
        if path == "/api/chat" and chat is not None:
            return chat(request)
        raise AssertionError(f"unexpected request {path}")

    return handler


def answer_for(request: httpx.Request) -> httpx.Response:
    """A schema-valid Ollama chat reply for whichever pass sent `request`."""
    prompt = json.loads(request.content)["messages"][-1]["content"]
    if "## Project\n" in prompt:
        data = {"overview": "Overview.", "architecture": "Architecture."}
    elif "## Module\n" in prompt:
        data = {"responsibility": "Responsibility.", "how_pieces_cooperate": "Cooperation."}
    else:
        data = FILE_ANSWER
    return httpx.Response(200, json={"message": {"content": json.dumps(data)}, "done_reason": "stop"})


# --- one builder per boundary row -----------------------------------------------------


def provider_unreachable(tmp_path, monkeypatch):
    root = make_project(tmp_path, HEALTHY)
    case = http_case(monkeypatch, root, ollama(reachable=False))
    case.before = snapshot_tree(root)
    return case, Expect(1, stderr=("Cannot reach Ollama at http://localhost:11434", "ollama serve"), documented=False, unchanged=True, calls=0)


def model_not_pulled(tmp_path, monkeypatch):
    root = make_project(tmp_path, HEALTHY)
    case = http_case(monkeypatch, root, ollama(models=("llama3:8b",)))
    case.before = snapshot_tree(root)
    return case, Expect(1, stderr=(f"Run: ollama pull {MODEL}",), documented=False, unchanged=True, calls=0)


def remote_not_acknowledged(tmp_path, monkeypatch):
    root = make_project(tmp_path, {**HEALTHY, "codewhy.toml": '[llm]\nprovider = "gemini"\nmodel = "m"\n'})
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key-for-tests")

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must never be reached
        raise AssertionError("no request may reach an unacknowledged remote endpoint")

    case = http_case(monkeypatch, root, handler)
    case.before = snapshot_tree(root)
    return case, Expect(
        1,
        stderr=("Your source code will be sent to generativelanguage.googleapis.com.", "--yes"),
        documented=False,
        unchanged=True,
        calls=0,
    )


def grammar_missing(tmp_path, monkeypatch):
    root = make_project(tmp_path, {**HEALTHY, "widget.ts": "export const w = 1;\n"})

    def missing(extension):
        raise ImportError("tree_sitter_typescript is not installed")

    monkeypatch.setattr(ts_treesitter, "_language_for", missing)
    return fake_case(monkeypatch, root), Expect(
        2, stdout=("skipped widget.ts (tree-sitter grammar unavailable: tree_sitter_typescript is not installed)",), calls=4
    )


def malformed_source(name: str, text: str, reason: str):
    def build(tmp_path, monkeypatch):
        root = make_project(tmp_path, {**HEALTHY, name: text})
        return fake_case(monkeypatch, root), Expect(2, stdout=(f"skipped {name} ({reason})",), calls=4)

    build.__name__ = f"malformed_{name.split('.')[-1]}"
    return build


def too_large_or_undecodable(tmp_path, monkeypatch):
    root = make_project(
        tmp_path,
        {**HEALTHY, "codewhy.toml": "[extraction]\nmax_file_bytes = 100\n", "big.py": "#" + "x" * 200 + "\n", "latin1.py": b"s = '\xe9'\n"},
    )
    return fake_case(monkeypatch, root), Expect(
        2, stdout=("skipped big.py (too_large)", "skipped latin1.py (undecodable)"), calls=4
    )


def cache_corrupt(tmp_path, monkeypatch):
    root = make_project(tmp_path, {**HEALTHY, ".codewhy/cache.db": b"\x00garbage" * 64})

    def rebuilt(root: Path, stdout: str) -> None:
        connection = sqlite3.connect(root / ".codewhy" / "cache.db")
        assert connection.execute("SELECT count(*) FROM pass_cache").fetchone()[0] == 4
        connection.close()

    return fake_case(monkeypatch, root), Expect(0, stderr=("warning: cache unreadable, rebuilding",), calls=4, check=rebuilt)


def cache_unknown_schema(tmp_path, monkeypatch):
    root = make_project(tmp_path, HEALTHY)
    root.joinpath(".codewhy").mkdir()
    connection = sqlite3.connect(root / ".codewhy" / "cache.db")
    connection.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL); INSERT INTO meta VALUES ('schema_version', '999');")
    connection.close()
    return fake_case(monkeypatch, root), Expect(0, stderr=("warning: cache unreadable, rebuilding",), calls=4)


def llm_output_malformed(tmp_path, monkeypatch):
    root = make_project(tmp_path, HEALTHY)
    # lib.py is the first file pass: both its attempts are not JSON; everything after is valid.
    replies = iter([b"not json at all", b"still not json"])

    def chat(request: httpx.Request) -> httpx.Response:
        content = next(replies, None)
        if content is not None:
            return httpx.Response(200, json={"message": {"content": content.decode()}, "done_reason": "stop"})
        return answer_for(request)

    def not_cached(root: Path, stdout: str) -> None:
        text = (root / "PROJECT_DOCS.md").read_text(encoding="utf-8")
        assert text.split("### `lib.py`\n\n#### Purpose\n\n", 1)[1].startswith("[generation failed]")
        connection = sqlite3.connect(root / ".codewhy" / "cache.db")
        assert connection.execute("SELECT count(*) FROM pass_cache WHERE subject = 'lib.py'").fetchone()[0] == 0
        connection.close()

    # 2 attempts for lib.py (one retry), then main.py, module ".", project.
    return http_case(monkeypatch, root, ollama(chat=chat)), Expect(
        2, stdout=("[1/4] file  lib.py  (failed)", "1 failed"), calls=5, check=not_cached
    )


def transient_provider_error(tmp_path, monkeypatch):
    root = make_project(tmp_path, {**HEALTHY, "codewhy.toml": "[llm]\nmax_transient_retries = 2\n"})
    failures = {"left": 3}  # the first request and both retries fail: lib.py gives up

    def chat(request: httpx.Request) -> httpx.Response:
        if failures["left"]:
            failures["left"] -= 1
            return httpx.Response(503, text="overloaded")
        return answer_for(request)

    def placeholder(root: Path, stdout: str) -> None:
        assert "[generation failed]" in (root / "PROJECT_DOCS.md").read_text(encoding="utf-8")

    return http_case(monkeypatch, root, ollama(chat=chat)), Expect(
        2, stdout=("[1/4] file  lib.py  (failed)",), calls=3 + 3, check=placeholder
    )


def output_not_codewhys(tmp_path, monkeypatch):
    root = make_project(tmp_path, {**HEALTHY, "PROJECT_DOCS.md": "# My own notes\n"})
    case = fake_case(monkeypatch, root)
    case.before = snapshot_tree(root)
    return case, Expect(1, stderr=("was not generated by codewhy", "--force"), documented=False, unchanged=True, calls=0)


def function_larger_than_chunk(tmp_path, monkeypatch):
    root = build_oversized_file(tmp_path, chunk_tokens=600)
    (root / "codewhy.toml").write_text("[llm]\ncontext_tokens = 2400\nmax_output_tokens = 256\n", encoding="utf-8")
    fake = FakeProvider()

    def truncated_not_failed(root: Path, stdout: str) -> None:
        assert any("[...truncated, " in call.prompt for call in fake.calls)
        assert "[generation failed]" not in (root / "PROJECT_DOCS.md").read_text(encoding="utf-8")

    return fake_case(monkeypatch, root, fake), Expect(0, stdout=(" 0 failed, 0 skipped.",), check=truncated_not_failed)


ROWS = [
    provider_unreachable,
    model_not_pulled,
    remote_not_acknowledged,
    grammar_missing,
    malformed_source("broken.py", "def broken(:\n", "SyntaxError: invalid syntax (line 1)"),
    malformed_source("broken.js", "function (\n", "syntax errors (first at line 1)"),
    malformed_source("broken.ts", "const x: = ;\n", "syntax errors (first at line 1)"),
    too_large_or_undecodable,
    cache_corrupt,
    cache_unknown_schema,
    llm_output_malformed,
    transient_provider_error,
    output_not_codewhys,
    function_larger_than_chunk,
]


@pytest.mark.parametrize("row", ROWS, ids=[row.__name__ for row in ROWS])
def test_failure_boundary(row, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case, expect = row(tmp_path, monkeypatch)
    result = run_cli(["scan", str(case.root), *case.args])
    # (a) exit code
    assert result.exit_code == expect.exit_code, (result.stdout, result.stderr)
    # (b) user-visible message, never a traceback
    for text in expect.stdout:
        assert text in result.stdout, (text, result.stdout)
    for text in expect.stderr:
        assert text in result.stderr, (text, result.stderr)
    assert "Traceback" not in result.stderr
    # (c) writes, (d) continued or stopped
    docs = case.root / "PROJECT_DOCS.md"
    if expect.documented:
        assert docs.is_file() and "generated_by: codewhy" in docs.read_text(encoding="utf-8")
    else:
        assert expect.unchanged or not docs.exists()
    if expect.unchanged:
        assert snapshot_tree(case.root) == case.before
    # (e) provider calls
    if expect.calls is None:
        assert case.calls() > 0
    else:
        assert case.calls() == expect.calls
    if expect.check is not None:
        expect.check(case.root, result.stdout)
