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


def build_broken_files(tmp_path: Path, max_file_bytes: int = 32) -> Path:
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
