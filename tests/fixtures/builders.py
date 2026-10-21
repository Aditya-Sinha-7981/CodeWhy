"""Fixture projects built in code (docs/TESTS.md -> Fixture projects).

These cannot be committed: the repo's own .gitignore would swallow
node_modules/, .venv/, and build/ directories.
"""

from __future__ import annotations

import warnings
from pathlib import Path

from tests.helpers import make_project

# Paths discovery must return as source files for build_ignored_content.
IGNORED_CONTENT_SOURCES = [
    "app/models.py",
    "build/release.py",
    "main.py",
    "scripts/deploy.py",
    "web/index.js",
]


def build_ignored_content(tmp_path: Path) -> Path:
    """A project mixing healthy sources with everything discovery must skip."""
    return make_project(
        tmp_path,
        {
            "main.py": "from app.models import Order\n\n\ndef run() -> None:\n    print(Order(3).total())\n",
            "app/models.py": (
                "class Order:\n"
                "    def __init__(self, qty: int) -> None:\n"
                "        self.qty = qty\n\n"
                "    def total(self) -> int:\n"
                "        return self.qty * 10\n"
            ),
            "web/index.js": "import { render } from 'react-dom';\nexport function start() { render(); }\n",
            # Listed in .gitignore, which codewhy must never read: still documented.
            "scripts/deploy.py": "import subprocess\n\nsubprocess.run(['echo', 'deploy'])\n",
            ".gitignore": "scripts/\nnode_modules/\n",
            # Built-in ignores.
            "node_modules/left-pad/index.js": "module.exports = (s) => s;\n",
            ".venv/lib/site.py": "import sys\n",
            "dist/bundle.min.js": "var a=1;\n",
            "types/api.d.ts": "export interface Api { get(): void }\n",
            # User-owned build/ folder, re-included via !build/ below.
            "build/release.py": "VERSION = '1.0'\n",
            # Custom .toolignore pattern.
            "generated/schema_pb2.py": "DESCRIPTOR = None\n",
            ".toolignore": "# project-specific\ngenerated/\n!build/\n",
        },
    )


# Paths of build_broken_files grouped by what discovery/extraction must do with them.
BROKEN_FILES_HEALTHY = ["healthy.js", "healthy.py"]
BROKEN_FILES_EMPTY = "__init__.py"
BROKEN_FILES_SYNTAX_ERROR = "syntax_error.py"
BROKEN_FILES_DISCOVERY_SKIPS = [("invalid_utf8.py", "undecodable"), ("oversized.py", "too_large")]


def build_broken_files(tmp_path: Path, max_file_bytes: int = 64) -> Path:
    """Broken and healthy files side by side; scan with `extraction.max_file_bytes = max_file_bytes`.

    Every file except `oversized.py` stays under `max_file_bytes`, so only the
    intended files are skipped for size.
    """
    root = tmp_path / "broken_files"
    root.mkdir()
    (root / "syntax_error.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "invalid_utf8.py").write_bytes(b"value = '\xff'\n")
    (root / "oversized.py").write_text("#" + ("x" * max_file_bytes) + "\n", encoding="utf-8")
    (root / "healthy.py").write_text("def healthy():\n    return True\n", encoding="utf-8")
    (root / "healthy.js").write_text("export const healthy = () => true;\n", encoding="utf-8")
    try:
        (root / "loop").symlink_to(root / "loop", target_is_directory=True)
    except (OSError, NotImplementedError) as exception:
        warnings.warn(
            f"symlink loop not created on this platform: {exception}",
            RuntimeWarning,
            stacklevel=2,
        )
    return root


# Names in build_oversized_file, for assertions.
OVERSIZED_FILE = "inventory/report.py"
OVERSIZED_MEDIUM_FUNCTIONS = [f"summarize_region_{n}" for n in range(1, 9)]
OVERSIZED_CLASS = "WarehouseLedger"
OVERSIZED_CLASS_METHODS = ["record_receipt", "record_shipment", "reconcile", "export_rows"]
OVERSIZED_FUNCTION = "build_quarterly_report"


def _metric_lines(target_tokens: int, indent: str, source: str) -> list[str]:
    """Body lines (about 3 characters per estimated token) totalling roughly `target_tokens`."""
    lines: list[str] = []
    size = 0
    step = 0
    while size < target_tokens * 3:
        step += 1
        line = f'{indent}totals["metric_{step}"] += {source}.get("field_{step}", 0) * {step}\n'
        lines.append(line)
        size += len(line)
    return lines


def build_oversized_file(tmp_path: Path, *, chunk_tokens: int) -> Path:
    """One Python file sized against `chunk_tokens` (a chunk's source budget in estimated tokens).

    - eight medium functions of about chunk_tokens / 4 each (several fit per chunk);
    - a class of about 2 * chunk_tokens: a header plus four methods of about
      chunk_tokens / 2 each, so it only fits when split at method boundaries;
    - one function of about 3 * chunk_tokens, larger than any chunk, so it is truncated;
    - ordinary imports at the top, and `main.py` importing the file.
    """
    parts = [
        '"""Inventory reporting: per-region totals, a warehouse ledger, and the quarterly report."""\n',
        "from collections import defaultdict\n",
        "import math\n",
        "\n",
    ]
    for number, name in enumerate(OVERSIZED_MEDIUM_FUNCTIONS, start=1):
        parts.append(f"\ndef {name}(records: list[dict]) -> dict:\n")
        parts.append(f'    """Totals for region {number}: quantities and revenue per metric."""\n')
        parts.append("    totals: dict[str, float] = defaultdict(float)\n")
        parts.append("    for record in records:\n")
        parts.extend(_metric_lines(chunk_tokens // 4, "        ", "record"))
        parts.append("    return dict(totals)\n\n")
    parts.append(f"\nclass {OVERSIZED_CLASS}:\n")
    parts.append('    """Running stock levels for one warehouse, updated as goods arrive and leave."""\n\n')
    parts.append("    currency = \"EUR\"\n")
    for method in OVERSIZED_CLASS_METHODS:
        parts.append(f"\n    def {method}(self, entry: dict) -> dict:\n")
        parts.append(f'        """Apply one {method.replace("_", " ")} to the ledger."""\n')
        parts.append("        totals: dict[str, float] = defaultdict(float)\n")
        parts.extend(_metric_lines(chunk_tokens // 2, "        ", "entry"))
        parts.append("        return dict(totals)\n")
    parts.append(f"\n\ndef {OVERSIZED_FUNCTION}(ledger: {OVERSIZED_CLASS}, records: list[dict]) -> dict:\n")
    parts.append('    """Combine every region and the ledger into one quarterly report."""\n')
    parts.append("    totals: dict[str, float] = defaultdict(float)\n")
    parts.append("    for record in records:\n")
    parts.extend(_metric_lines(chunk_tokens * 3, "        ", "record"))
    parts.append("    totals[\"scale\"] = math.log10(max(len(records), 1))\n")
    parts.append("    return dict(totals)\n")
    root = tmp_path / "oversized_file"
    return make_project(
        root,
        {
            OVERSIZED_FILE: "".join(parts),
            "inventory/__init__.py": "",
            "main.py": (
                "from inventory.report import WarehouseLedger, build_quarterly_report\n\n\n"
                "def main() -> None:\n    print(build_quarterly_report(WarehouseLedger(), []))\n"
            ),
        },
    )
