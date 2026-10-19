from __future__ import annotations

import warnings
from pathlib import Path


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
