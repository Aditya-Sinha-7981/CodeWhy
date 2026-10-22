"""Terminal-side helpers for `scan`: progress lines, warnings, summary, exit codes.

Nothing here touches the pipeline; `cli.py` wires a `ProgressReporter` in as the
`on_item`/`on_stage` callbacks. On a TTY a rich spinner shows what is running
(stage and per-request detail) above which item lines are printed; anywhere
else output is plain lines with no control codes. Text bound for the terminal
falls back to ASCII when the console encoding cannot represent it; files are
always UTF-8 and never go through here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from rich.console import Console
    from rich.status import Status

    from codewhy.pipeline import RunResult

EXIT_OK = 0
EXIT_FATAL = 1
EXIT_INCOMPLETE = 2
EXIT_INTERRUPTED = 130
INTERRUPT_MESSAGE = "Interrupted. Progress is saved in the cache; re-run to resume."

_ASCII = {"—": "-", "–": "-", "→": "->", "…": "...", "≈": "~", "✓": "ok"}


def exit_code_for(run_result: RunResult | None, skips: Sequence[object], fatal: bool) -> int:
    """The one place `scan` decides its exit code (interrupts use EXIT_INTERRUPTED).

    `skips` are files skipped for parse/size/decode/read problems; ignored and
    unsupported files never appear there.
    """
    if fatal or run_result is None:
        return EXIT_FATAL
    if skips or run_result.counts.get("failed", 0):
        return EXIT_INCOMPLETE
    return EXIT_OK


def terminal_text(text: str, encoding: str | None) -> str:
    """`text` as the console can show it: known symbols become ASCII, anything else `?`."""
    encoding = encoding or "utf-8"
    try:
        text.encode(encoding)
        return text
    except (UnicodeEncodeError, LookupError):
        pass
    for symbol, plain in _ASCII.items():
        text = text.replace(symbol, plain)
    try:
        return text.encode(encoding, errors="replace").decode(encoding)
    except LookupError:
        return text.encode("ascii", errors="replace").decode("ascii")


def item_line(index: int, total: int, pass_name: str, subject: str, status: str) -> str:
    return f"[{index}/{total}] {pass_name}  {subject}  ({status})"


def summary_line(files: int, counts: dict[str, int], skipped: int, output: str | None, tokens: int | None, level: int) -> str:
    head = (
        f"{files} files, {counts.get('cached', 0)} cached, {counts.get('generated', 0)} generated, "
        f"{counts.get('failed', 0)} failed, {skipped} skipped."
    )
    if output is None or tokens is None:
        return f"{head} docs-tree output written (level {level})."
    return f"{head} {output} ≈ {tokens:,} tokens (level {level})."


class ProgressReporter:
    """`on_item` / `on_stage` callbacks for one scan.

    `echo` writes one already-terminal-safe line to stdout; `console` (TTY only)
    drives the spinner. With `quiet`, nothing is printed at all.
    """

    def __init__(
        self,
        *,
        quiet: bool,
        echo: Callable[[str], None],
        console: Console | None = None,
        project_label: str = "project",
    ) -> None:
        self.quiet = quiet
        self.echo = echo
        self.console = console
        self.project_label = project_label
        self.total = 0
        self.index = 0
        self._status: Status | None = None

    def start(self, text: str) -> None:
        if self.quiet or self.console is None:
            return
        self._status = self.console.status(text, spinner="dots")
        self._status.start()

    def stop(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def set_total(self, total: int) -> None:
        self.total = total

    def on_stage(self, stage: str, detail: str) -> None:
        # Stage and request detail only feed the spinner; plain output stays one line per item.
        if self._status is not None:
            self._status.update(f"{stage}: {detail}")

    def on_item(self, pass_name: str, subject: str, status: str) -> None:
        self.index += 1
        if self.quiet:
            return
        shown = subject if subject or pass_name != "project" else self.project_label
        line = item_line(self.index, self.total, pass_name, shown, status)
        if self.console is not None:
            self.console.print(line, markup=False, highlight=False, soft_wrap=True)
            if self._status is not None and self.index < self.total:
                self._status.update(f"[{self.index + 1}/{self.total}] working…")
        else:
            self.echo(line)


class _Collector(logging.Handler):
    def __init__(self, keep: Callable[[logging.LogRecord], bool]) -> None:
        super().__init__(level=logging.WARNING)
        self.keep = keep
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if self.keep(record):
            message = record.getMessage()
            if message not in self.messages:
                self.messages.append(message)


def _not_a_skip(record: logging.LogRecord) -> bool:
    # Discovery skips are reported as `skipped <path> (<reason>)` lines already.
    return not (record.name == "codewhy.ignore" and record.msg.startswith("skipping "))


@contextmanager
def collected_warnings(logger_name: str = "codewhy") -> Iterator[list[str]]:
    """Collect library warnings (instead of printing them as they happen) for one command."""
    logger = logging.getLogger(logger_name)
    handler = _Collector(_not_a_skip)
    logger.addHandler(handler)
    try:
        yield handler.messages
    finally:
        logger.removeHandler(handler)


def stream_encoding(stream: TextIO) -> str | None:
    return getattr(stream, "encoding", None)
