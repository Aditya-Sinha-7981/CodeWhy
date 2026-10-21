"""The file pass: why one file exists (docs/ARCHITECTURE.md -> `file_pass`, Chunking).

Order of decisions: an effectively empty file gets a fixed purpose with no
call; a prompt with the full source that fits the budget is sent as one call;
anything larger is chunked along top-level units, each chunk summarized, and the
chunk purposes merged by exactly one combine call.
"""

from __future__ import annotations

from collections.abc import Sequence

from codewhy.config import Config
from codewhy.extraction.model import FileStructure
from codewhy.llm.budget import estimate_tokens, fits
from codewhy.llm.chunking import CONTEXT_TOO_SMALL, Chunk, ContextTooSmall, plan_chunks, source_lines
from codewhy.llm.provider import Provider
from codewhy.summarize.prompts import chunk_prompt, combine_prompt, file_prompt, longest_backtick_run, top_level_names
from codewhy.summarize.result import Clock, PassFailed, PassResult, RequestCallback, call_pass, pass_budget
from codewhy.summarize.schemas import FilePass
from codewhy.text import first_sentence

EMPTY_FILE_PURPOSE = "Empty file."


def is_effectively_empty(text: str, language: str) -> bool:
    """True when `text` has no line that is neither blank nor a comment.

    Python comments are `#` lines; JS/TS comments are `//` lines and anything
    inside `/* */`. Only comments that start a line (after other comments) count,
    so comment markers inside strings never hide code.
    """
    in_block = False
    for line in text.split("\n"):
        rest = line.strip()
        while rest:
            if language == "python":
                if not rest.startswith("#"):
                    return False
                break
            if in_block:
                end = rest.find("*/")
                if end < 0:
                    break
                in_block = False
                rest = rest[end + 2 :].strip()
            elif rest.startswith("//"):
                break
            elif rest.startswith("/*"):
                in_block = True
                rest = rest[2:]
            else:
                return False
    return True


def chunk_overhead(fs: FileStructure, names: Sequence[str], imports: Sequence[str], imported_by: Sequence[str]) -> int:
    """Estimated tokens of a chunk prompt without its source, sized for the worst case.

    The placeholder chunk uses the largest line numbers and a text as long as the
    file's longest backtick run (which sets the code-fence length) plus one
    character without a newline, so any real chunk's prompt is at most this
    overhead plus the chunk text's own estimate.
    """
    last = max(fs.line_count, len(source_lines(fs.source_text)), 1)
    placeholder = Chunk(last, last, "`" * longest_backtick_run(fs.source_text) + "x")
    return estimate_tokens(chunk_prompt(fs, placeholder, names, imports, imported_by))


def run_file_pass(
    fs: FileStructure,
    imports: Sequence[str],
    imported_by: Sequence[str],
    *,
    provider: Provider,
    config: Config,
    clock: Clock,
    on_request: RequestCallback | None = None,
) -> PassResult:
    if is_effectively_empty(fs.source_text, fs.language):
        return PassResult.ok(FilePass(purpose=EMPTY_FILE_PURPOSE), clock())
    try:
        data = _summarize(fs, imports, imported_by, provider=provider, config=config, on_request=on_request)
    except ContextTooSmall:
        return PassResult.failed(CONTEXT_TOO_SMALL, clock())
    except PassFailed as exc:
        return PassResult.failed(exc.reason, clock())
    return PassResult.ok(data, clock())


def _summarize(
    fs: FileStructure,
    imports: Sequence[str],
    imported_by: Sequence[str],
    *,
    provider: Provider,
    config: Config,
    on_request: RequestCallback | None = None,
) -> FilePass:
    budget = pass_budget(config)
    max_out = config.llm.effective_max_output_tokens

    def call(prompt: str) -> FilePass:
        return call_pass(provider, prompt, FilePass, budget=budget, max_output_tokens=max_out, on_request=on_request)

    prompt = file_prompt(fs, imports, imported_by, include_source=True)
    if fits(prompt, budget):
        return call(prompt)

    names = top_level_names(fs)
    chunks = plan_chunks(fs, budget, chunk_overhead(fs, names, imports, imported_by))
    purposes: list[str] = []
    for number, chunk in enumerate(chunks, start=1):
        try:
            purposes.append(call(chunk_prompt(fs, chunk, names, imports, imported_by)).purpose)
        except PassFailed as exc:
            raise PassFailed(f"chunk {number} failed: {exc.reason}") from None

    combined = combine_prompt(fs, purposes, imports, imported_by)
    if not fits(combined, budget):
        combined = combine_prompt(fs, [first_sentence(p) for p in purposes], imports, imported_by)
    if not fits(combined, budget):
        raise ContextTooSmall()
    return call(combined)
