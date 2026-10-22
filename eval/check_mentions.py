"""Mention check: do the names the model wrote in its prose actually exist in the project?

    python eval/check_mentions.py <project folder> <PROJECT_DOCS.md> [--json]

Only LLM prose is checked: the paragraphs under Purpose, Notes, Responsibility,
How the pieces cooperate, Overview and Architecture. Deterministic sections
(Depends On, Used By, Defined Here, file lists) come from parsing and are
skipped. Every `backticked` span in that prose is a mention:

- a path-like mention (contains `/` or ends in a file extension) exists when it
  names a project file or folder, or is the tail of one (`cli.py` for
  `src/codewhy/cli.py`);
- any other mention is reduced to its last dotted part without call
  parentheses (`CacheDB.peek()` -> `peek`) and exists when it is a name defined
  in the extraction output (class, method, function, type) or appears as a
  whole word anywhere in the project's source.

This is a metric, not a gate: a mention can exist and still be described wrongly,
and an illustrative name (a placeholder, a CLI flag) counts as a miss. Read the
miss list.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from codewhy.config import resolve_config
from codewhy.project import build_project

PROSE_HEADINGS = {"Purpose", "Notes", "Responsibility", "How the pieces cooperate", "Overview", "Architecture"}
HEADING = re.compile(r"^(#{1,6}) (.*)$")
BACKTICKED = re.compile(r"`([^`\n]+)`")
EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,5}$")
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class Report:
    total: int = 0
    found: int = 0
    misses: list[str] = field(default_factory=list)

    @property
    def percent(self) -> float:
        return 100.0 * self.found / self.total if self.total else 100.0


def prose_blocks(markdown: str) -> list[str]:
    """Paragraph text under the LLM-prose headings (the frontmatter and fences are skipped)."""
    blocks: list[str] = []
    current: list[str] | None = None
    in_fence = False
    for line in markdown.split("\n"):
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        match = HEADING.match(line) if not in_fence else None
        if match:
            if current:
                blocks.append("\n".join(current))
            current = [] if match.group(2).strip() in PROSE_HEADINGS else None
            continue
        if current is not None:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def mentions(markdown: str) -> list[str]:
    return [span.strip() for block in prose_blocks(markdown) for span in BACKTICKED.findall(block) if span.strip()]


def _identifier(mention: str) -> str | None:
    text = re.sub(r"\(.*\)$", "", mention.strip())
    text = re.sub(r"^(def|class|interface|type|async def)\s+", "", text)
    parts = [p for p in re.split(r"[.:]", text) if p]
    if not parts:
        return None
    last = parts[-1].strip()
    return last if WORD.fullmatch(last) else None


def check(root: Path, markdown: str) -> Report:
    project = build_project(root, resolve_config(root))
    paths = set(project.files) | set(project.other_files) | {s.path for s in project.skips}
    folders = {"/".join(p.split("/")[:n]) for p in paths for n in range(1, p.count("/") + 1)}
    defined: set[str] = set()
    words: set[str] = set()
    for fs in project.structures.values():
        defined.update(f.name for f in fs.functions)
        defined.update(t.name for t in fs.types)
        for cls in fs.classes:
            defined.add(cls.name)
            defined.update(m.name for m in cls.methods)
        words.update(WORD.findall(fs.source_text))

    report = Report()
    for mention in mentions(markdown):
        report.total += 1
        candidate = mention.strip().strip("/").removeprefix("./")
        if "/" in candidate or EXTENSION.search(candidate):
            ok = candidate in paths or candidate in folders or any(p.endswith("/" + candidate) for p in paths)
        else:
            name = _identifier(candidate)
            ok = name is not None and (name in defined or name in words)
        if ok:
            report.found += 1
        else:
            report.misses.append(mention)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that backticked names in LLM prose exist in the project.")
    parser.add_argument("project", type=Path)
    parser.add_argument("docs", type=Path)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    report = check(args.project, args.docs.read_text(encoding="utf-8"))
    if args.json:
        print(json.dumps({"total": report.total, "found": report.found, "percent": round(report.percent, 1), "misses": report.misses}))
        return 0
    print(f"mentions in LLM prose: {report.total}; found in the project: {report.found} ({report.percent:.1f}%)")
    for miss in sorted(set(report.misses)):
        print(f"  miss: {miss} (x{report.misses.count(miss)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
