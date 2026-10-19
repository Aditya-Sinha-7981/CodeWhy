import os
from pathlib import Path

import pytest

from codewhy.config import Config, resolve_config
from codewhy.ignore import (
    BUILTIN_IGNORE_PATTERNS,
    DiscoveryError,
    DiscoveryResult,
    discover,
)
from tests.fixtures.builders import IGNORED_CONTENT_SOURCES, build_ignored_content
from tests.helpers import make_project


def run(root: Path, overrides: dict[str, object] | None = None) -> DiscoveryResult:
    return discover(root, resolve_config(root, overrides))


def source_paths(result: DiscoveryResult) -> list[str]:
    return [f.path for f in result.files]


# --- ignore rules ---------------------------------------------------------


def test_builtins_applied_without_toolignore(tmp_path: Path) -> None:
    files = {"main.py": "x = 1\n"}
    for pattern in BUILTIN_IGNORE_PATTERNS:
        if pattern.endswith("/"):
            files[f"{pattern}inner.py"] = "x = 1\n"
            files[f"nested/{pattern}deep.py"] = "x = 1\n"
        else:
            files[pattern.replace("*", "lib")] = "x = 1\n"
    make_project(tmp_path, files)
    result = run(tmp_path)
    assert source_paths(result) == ["main.py"]
    assert result.other_files == []


def test_ignored_content_fixture(tmp_path: Path) -> None:
    root = build_ignored_content(tmp_path)
    result = run(root)
    assert source_paths(result) == IGNORED_CONTENT_SOURCES
    assert result.other_files == [".gitignore"]
    assert result.skips == []


def test_toolignore_extends_builtins(tmp_path: Path) -> None:
    make_project(
        tmp_path,
        {
            "main.py": "",
            "generated/a_pb2.py": "",
            "src/x.generated.ts": "",
            "src/x.ts": "",
            "node_modules/m/index.js": "",
            ".toolignore": "generated/\n*.generated.ts\n",
        },
    )
    assert source_paths(run(tmp_path)) == ["main.py", "src/x.ts"]


def test_negated_directory_reincludes_builtin(tmp_path: Path) -> None:
    make_project(tmp_path, {"build/tool.py": "", "dist/out.js": "", ".toolignore": "!build/\n"})
    assert source_paths(run(tmp_path)) == ["build/tool.py"]


def test_negated_file_inside_ignored_directory_has_no_effect(tmp_path: Path) -> None:
    # Pinned behavior (same as git): build/ is pruned, so the walker never sees keep.py.
    make_project(tmp_path, {"main.py": "", "build/keep.py": "", ".toolignore": "!build/keep.py\n"})
    result = run(tmp_path)
    assert source_paths(result) == ["main.py"]
    assert result.other_files == []


def test_gitignore_has_zero_effect(tmp_path: Path) -> None:
    make_project(
        tmp_path,
        {"main.py": "", "local_script.py": "", "secret/notes.py": "", ".gitignore": "local_script.py\nsecret/\n*.py\n"},
    )
    result = run(tmp_path)
    assert source_paths(result) == ["local_script.py", "main.py", "secret/notes.py"]
    assert result.other_files == [".gitignore"]


def test_invalid_toolignore_pattern_warns_and_continues(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    make_project(tmp_path, {"main.py": "", "gen/a.py": "", ".toolignore": "!\ngen/\n"})
    assert source_paths(run(tmp_path)) == ["main.py"]
    assert any("invalid pattern" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)


def test_toolignore_comments_blank_lines_and_crlf(tmp_path: Path) -> None:
    make_project(tmp_path, {"a.py": "", "b.py": "", "#c.py": "", ".toolignore": b"\xef\xbb\xbf# comment\r\n\r\nb.py\r\n"})
    assert source_paths(run(tmp_path)) == ["#c.py", "a.py"]


# --- always-excluded artifacts ---------------------------------------------

ARTIFACTS = {
    ".codewhy/cache.db": b"SQLite format 3\x00",
    ".codewhy/stray.py": "x = 1\n",
    "codewhy-docs/PROJECT_OVERVIEW.md": "---\ngenerated_by: codewhy v0.1.0\n---\n",
    "codewhy-docs/main.py.md": "doc\n",
    "codewhy-docs/copied.py": "x = 1\n",
    "PROJECT_DOCS.md": "---\ngenerated_by: codewhy v0.1.0\n---\n",
    "codewhy.toml": "",
}


def test_always_excluded_artifacts_never_discovered(tmp_path: Path) -> None:
    make_project(tmp_path, {"main.py": "", **ARTIFACTS, ".toolignore": "# nothing\n"})
    result = run(tmp_path)
    assert source_paths(result) == ["main.py"]
    assert result.other_files == []


def test_always_excluded_artifacts_cannot_be_negated(tmp_path: Path) -> None:
    negations = "!.codewhy/\n!codewhy-docs/\n!PROJECT_DOCS.md\n!codewhy.toml\n!.toolignore\n!*\n"
    make_project(tmp_path, {"main.py": "", **ARTIFACTS, ".toolignore": negations})
    result = run(tmp_path)
    assert source_paths(result) == ["main.py"]
    assert result.other_files == []


def test_configured_output_names_are_excluded(tmp_path: Path) -> None:
    make_project(
        tmp_path,
        {
            "main.py": "",
            "out/docs/x.py": "",
            "out/keep.py": "",
            "ALL.md": "",
            "PROJECT_DOCS.md": "",
            "codewhy.toml": '[output]\ndir = "out/docs"\nsingle_file_name = "ALL.md"\n',
        },
    )
    result = run(tmp_path)
    assert source_paths(result) == ["main.py", "out/keep.py"]
    # Only the configured names are codewhy's; the default name is an ordinary file now.
    assert result.other_files == ["PROJECT_DOCS.md"]


def test_root_only_artifacts_are_ordinary_files_in_subfolders(tmp_path: Path) -> None:
    make_project(tmp_path, {"sub/codewhy.toml": "", "sub/.toolignore": "", "sub/PROJECT_DOCS.md": ""})
    assert run(tmp_path).other_files == ["sub/.toolignore", "sub/PROJECT_DOCS.md", "sub/codewhy.toml"]


def test_self_pollution_regression(tmp_path: Path) -> None:
    root = build_ignored_content(tmp_path)
    first = run(root)
    # What later tasks write into the tree: outputs in both modes and the cache.
    make_project(
        root,
        {
            "PROJECT_DOCS.md": "---\ngenerated_by: codewhy v0.1.0\n---\n# docs\n",
            "codewhy-docs/PROJECT_OVERVIEW.md": "---\ngenerated_by: codewhy v0.1.0\n---\n",
            "codewhy-docs/_module.md": "x\n",
            "codewhy-docs/app/models.py.md": "x\n",
            ".codewhy/cache.db": b"\x00\x01",
            ".codewhy/cache.db-journal": b"\x00",
            "codewhy.toml": "",
        },
    )
    assert run(root) == first


# --- walking safety -------------------------------------------------------


def test_ignored_directories_are_pruned_not_walked(tmp_path: Path, mocker) -> None:
    make_project(tmp_path, {"main.py": "", "node_modules/a/b/c.js": "", "pkg/__pycache__/m.pyc": b"\x00"})
    locked = tmp_path / "node_modules" / "locked"
    locked.mkdir()
    (locked / "x.js").write_text("")
    can_lock = os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() != 0
    if can_lock:
        locked.chmod(0)
    spy = mocker.spy(os, "scandir")
    try:
        result = run(tmp_path)
    finally:
        if can_lock:
            locked.chmod(0o700)
    scanned = {Path(call.args[0]).relative_to(tmp_path).as_posix() for call in spy.call_args_list}
    assert scanned == {".", "pkg"}
    assert source_paths(result) == ["main.py"]


def test_unreadable_directory_outside_ignores_does_not_stop_discovery(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    if os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() == 0:
        pytest.skip("directory permissions cannot be revoked on this platform/user")
    make_project(tmp_path, {"main.py": "", "private/x.py": ""})
    (tmp_path / "private").chmod(0)
    try:
        result = run(tmp_path)
    finally:
        (tmp_path / "private").chmod(0o700)
    assert source_paths(result) == ["main.py"]
    assert any("private" in r.getMessage() for r in caplog.records)


def _symlink(link: Path, target: Path, *, is_dir: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlinks here: {exc}")


def test_symlinks_are_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    make_project(outside, {"secret.py": "TOKEN = 1\n", "lib/util.py": ""})
    make_project(root, {"main.py": "", "pkg/mod.py": ""})
    _symlink(root / "loop", root, is_dir=True)  # loop back to the root
    _symlink(root / "pkg" / "parent", root / "pkg", is_dir=True)  # self loop
    _symlink(root / "escape", outside / "lib", is_dir=True)  # directory outside root
    _symlink(root / "secret.py", outside / "secret.py", is_dir=False)  # file outside root
    _symlink(root / "alias.py", root / "main.py", is_dir=False)  # file inside root
    result = run(root)
    assert source_paths(result) == ["main.py", "pkg/mod.py"]
    assert result.other_files == []
    assert result.skips == []


# --- guards and text ------------------------------------------------------


def test_too_large_file_is_skipped(tmp_path: Path) -> None:
    make_project(tmp_path, {"small.py": "x" * 10, "big.py": "x" * 11, "big.txt": "x" * 100})
    result = run(tmp_path, {"extraction.max_file_bytes": 10})
    assert source_paths(result) == ["small.py"]
    assert [(s.path, s.reason) for s in result.skips] == [("big.py", "too_large")]
    assert "11" in result.skips[0].detail
    assert result.other_files == ["big.txt"]  # size guard applies to supported files only


def test_undecodable_file_is_skipped(tmp_path: Path) -> None:
    make_project(tmp_path, {"ok.py": "x = 1\n", "bad.py": b"x = '\xff\xfe'\n", "latin1.js": b"// caf\xe9\n"})
    result = run(tmp_path)
    assert source_paths(result) == ["ok.py"]
    assert [(s.path, s.reason) for s in result.skips] == [("bad.py", "undecodable"), ("latin1.js", "undecodable")]
    assert all("\xff" not in s.detail for s in result.skips)


def test_unreadable_file_is_skipped(tmp_path: Path) -> None:
    if os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() == 0:
        pytest.skip("file permissions cannot be revoked on this platform/user")
    make_project(tmp_path, {"ok.py": "", "locked.py": "x = 1\n"})
    (tmp_path / "locked.py").chmod(0)
    try:
        result = run(tmp_path)
    finally:
        (tmp_path / "locked.py").chmod(0o600)
    assert source_paths(result) == ["ok.py"]
    assert [(s.path, s.reason) for s in result.skips] == [("locked.py", "unreadable")]


def test_bom_stripped_and_size_is_on_disk(tmp_path: Path) -> None:
    make_project(tmp_path, {"bom.py": b"\xef\xbb\xbfx = 1\n"})
    (source,) = run(tmp_path).files
    assert source.text == "x = 1\n"
    assert source.size == 9


def test_newlines_normalized(tmp_path: Path) -> None:
    make_project(tmp_path, {"crlf.py": b"a = 1\r\nb = 2\r\n", "cr.js": b"a\rb\r", "mixed.ts": b"a\r\nb\rc\n"})
    texts = {f.path: f.text for f in run(tmp_path).files}
    assert texts == {"cr.js": "a\nb\n", "crlf.py": "a = 1\nb = 2\n", "mixed.ts": "a\nb\nc\n"}
    assert {f.path: f.size for f in run(tmp_path).files}["crlf.py"] == 14


EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
}


def test_extension_mapping(tmp_path: Path) -> None:
    files = {f"f{ext}": "" for ext in EXTENSIONS}
    files.update({"types.d.ts": "", "src/api.d.ts": "", "app.min.js": "", "notes.md": "", "Makefile": "", "data.json": ""})
    make_project(tmp_path, files)
    result = run(tmp_path)
    assert {f.path: f.language for f in result.files} == {f"f{ext}": lang for ext, lang in EXTENSIONS.items()}
    assert result.other_files == ["Makefile", "data.json", "notes.md"]


def test_disabled_language_is_other_file(tmp_path: Path) -> None:
    make_project(tmp_path, {"a.py": "", "b.js": "", "c.tsx": "", "d.txt": ""})
    result = run(tmp_path, {"extraction.languages": ["python"]})
    assert source_paths(result) == ["a.py"]
    assert result.other_files == ["b.js", "c.tsx", "d.txt"]


# --- determinism ------------------------------------------------------------

TREE = {
    "z.py": "",
    "a-b.py": "",
    "a/b.py": "",
    "a/c/d.ts": "",
    "B.js": "",
    "readme.md": "",
    "a/z.txt": "",
    "big.py": "x" * 50,
    "bad.js": b"\xff",
}


def test_ordering_is_deterministic(tmp_path: Path) -> None:
    forward = make_project(tmp_path / "forward", TREE)
    backward = make_project(tmp_path / "backward", dict(reversed(list(TREE.items()))))
    overrides = {"extraction.max_file_bytes": 20}
    a, b = run(forward, overrides), run(backward, overrides)
    assert a == b
    assert source_paths(a) == ["B.js", "a-b.py", "a/b.py", "a/c/d.ts", "z.py"]
    assert a.other_files == ["a/z.txt", "readme.md"]
    assert [s.path for s in a.skips] == ["bad.js", "big.py"]


def test_paths_are_posix_relative(tmp_path: Path) -> None:
    make_project(tmp_path, {"a/b/c.py": "", "a/b/d.txt": ""})
    result = run(tmp_path)
    assert source_paths(result) == ["a/b/c.py"]
    assert result.other_files == ["a/b/d.txt"]


# --- root validation ------------------------------------------------------


def test_missing_root_raises(tmp_path: Path) -> None:
    with pytest.raises(DiscoveryError, match="does not exist"):
        discover(tmp_path / "nope", Config())


def test_file_root_raises(tmp_path: Path) -> None:
    make_project(tmp_path, {"f.py": ""})
    with pytest.raises(DiscoveryError, match="not a directory"):
        discover(tmp_path / "f.py", Config())
