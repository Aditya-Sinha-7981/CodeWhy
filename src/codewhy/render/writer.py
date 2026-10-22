"""Atomic per-file output writes and cleanup of temp files left by a crashed run.

`write_atomic` writes the whole document to `<name>.<16 hex>.tmp` in the target's
own folder, fsyncs it, then `os.replace`s it over the target, so a crash or
Ctrl-C leaves either the previous document or the new one, never a truncated
one. Atomicity is per file: a docs-tree run interrupted between files can leave
some documents updated and others not; the next run repairs that.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)

TEMP_SUFFIX = ".tmp"
_TEMP_NAME = re.compile(r"^.+\.[0-9a-f]{16}\.tmp$")


def temp_path_for(path: Path) -> Path:
    return path.with_name(f"{path.name}.{secrets.token_hex(8)}{TEMP_SUFFIX}")


def is_temp_name(name: str) -> bool:
    """True only for names this writer creates."""
    return bool(_TEMP_NAME.match(name))


def write_atomic(path: Path, text: str) -> None:
    """Write `text` as UTF-8 with `\\n` newlines to `path`, all or nothing."""
    data = text.replace("\r\n", "\n").encode("utf-8")
    temp = temp_path_for(path)
    try:
        with temp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        # Includes KeyboardInterrupt: never leave our temp file behind.
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            logger.debug("could not remove temp file %s", temp)
        raise


def remove_stale_temps(root: Path, output_dir: str) -> list[Path]:
    """Delete writer temp files in `root` and under `root/output_dir`; never follow symlinks.

    Only files whose names match the writer's temp pattern are touched. Returns
    the removed paths (sorted).
    """
    removed: list[Path] = []
    candidates: list[Path] = []
    try:
        candidates.extend(entry for entry in root.iterdir() if entry.is_file() and not entry.is_symlink())
    except OSError:
        return removed
    tree = root / Path(*output_dir.split("/"))
    if tree.is_dir() and not tree.is_symlink():
        for base, dirs, names in os.walk(tree, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
            candidates.extend(Path(base) / name for name in names)
    for candidate in sorted(candidates):
        if not is_temp_name(candidate.name) or candidate.is_symlink():
            continue
        try:
            candidate.unlink()
        except OSError as exc:
            logger.warning("could not remove stale temp file %s: %s", candidate, exc.strerror or exc)
            continue
        removed.append(candidate)
    return removed
