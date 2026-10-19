# Work log — CW-01 scaffold, config, discovery, module model

## 2026-10-19

### Plan (written before the first code change)

Read: `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/TESTS.md`, `tasks/ALL-TASKS.md`,
`tasks/01-scaffold-config-discovery.md`, `members/aditya.md`, and the config-related
parts of tasks 02–05 (to keep the APIs they consume stable). `git status`: clean, on
`main`. No earlier logs exist.

Order of work:

1. `pyproject.toml` (hatchling, src layout, dynamic version from `__init__.py`),
   `.gitignore` fixes, `README.md` stub; create `.venv`, `pip install -e ".[dev]"`.
2. `config.py`: pydantic v2 models (`extra="forbid"`, strict), `load_config`,
   `resolve_config`, `render_toml`, `ConfigError`; then `tests/test_config.py`.
3. `ignore.py`: `discover` with built-ins + `.toolignore` in one ordered pathspec,
   always-excluded artifacts, pruning, symlink/size/decode guards; then
   `tests/test_ignore.py` and `tests/fixtures/builders.py`.
4. `project.py`: `ProjectModel`/`Module`/`build_project`; `tests/test_project.py`.
5. `cli.py` + templates + `codewhy.toml.example`: `--version`, `init`,
   `config show`, `scan` stub, shared error helper; `tests/test_cli_init_config.py`.
6. Full test run, manual verification, wheel content check, second Python version.

### Environment

macOS (Darwin 25.6, arm64). System Python 3.14.7 for `.venv`; Python 3.11.11 via
`uv` for a second-version run. Installed versions: typer 0.27.3 (vendors its own
click; `CliRunner` captures stderr separately), rich 15.0.0, pathspec 1.1.1,
pydantic 2.14.1, pytest 9.1.1, pytest-mock 3.17.0.

### Decisions (later tasks depend on these)

- **Version source:** constant `__version__ = "0.1.0"` in `src/codewhy/__init__.py`;
  `pyproject.toml` reads it through `[tool.hatch.version] path`.
- **pathspec class:** `pathspec.GitIgnoreSpec` (available in 1.1.1). Built-ins first,
  then `.toolignore` lines, compiled as one ordered spec. Each `.toolignore` line
  is compiled alone first; a line pathspec rejects (observed: a lone `!`, a
  trailing `\`) is dropped with a `logging` warning naming the line number.
- **Pinned negation behavior (tested):** `!build/` re-includes the built-in
  `build/`. `!build/keep.py` does **not** re-include `build/keep.py`, because
  `build/` still matches as a directory and is pruned, so the walker never looks
  inside it (same as git). Observed directly in pathspec: with `["build/",
  "!build/keep.py"]`, `match_file("build/")` is True and
  `match_file("build/keep.py")` is False, so pruning is what enforces git
  semantics. Directories are tested with a trailing `/`.
- **`llm.model` defaults to `None`**, not `"qwen2.5-coder:14b"`. Task 04 must
  detect "model missing for a hosted provider", which is impossible if the
  ollama default is stored as an ordinary value. `LLMConfig.effective_model`
  returns `qwen2.5-coder:14b` for `ollama` and `None` otherwise, mirroring
  `effective_context_tokens`.
- **Unset-means-`None` keys:** `model`, `base_url`, `api_key_env`,
  `context_tokens`, `structured_output`, `send_seed`, `reasoning_effort`. Task 04's
  preset application can then tell an explicit user value (which must win) from a
  default. The architecture gives no non-preset default for these.
- **Template:** `codewhy.toml` follows the architecture example, except that
  `model` and `context_tokens` are **commented out**, with their provider-specific
  defaults explained. Uncommented, `init` followed by `provider = "gemini"` would
  silently keep the ollama model and the 16384 context. The template resolves to
  exactly `Config()` (tested). No architecture edit: the architecture's block
  shows the defaults, and the template still lists every key.
- **Config strictness:** pydantic `strict=True` + `extra="forbid"`. `"1"` is not an
  int, `"yes"` is not a bool; ints are accepted for float `temperature`.
- **`load_config` → `RawConfig(data, source_path)`:** the file's contents exactly
  as written, validated in full (unknown keys and bad values collected in one
  pydantic pass, so they are reported together). `resolve_config` merges
  overrides and validates again; errors there are labeled "command-line options".
  Model-level checks (dir/file collision) run only once field checks pass.
- **Path rules** (`output.dir`, `output.single_file_name`, `project.entry_point`):
  non-empty; no `\` (must use `/`); not POSIX- or Windows-absolute, no drive; no
  `..`; not the root itself. Stored normalized (`./a/b/` → `a/b`). "Collide" means
  equal, or one is inside the other.
- **Always-excluded:** `.codewhy/` is excluded at **any** depth (it is only ever
  codewhy's cache). The output `dir` is matched as a project-relative path.
  `single_file_name`, `codewhy.toml`, `.toolignore` are excluded at the root only,
  so in subfolders they are ordinary "other files" (tested). All of these are
  checked before the ignore spec, so no negation can re-include them.
- **Symlinks** (files and directories) and Windows junctions (3.12+
  `DirEntry.is_junction`) are skipped silently (debug log), not recorded as skips.
- **Unreadable directory** that is not ignored: warning via `logging`, walk
  continues; not recorded as a `Skip` (skips are per supported file).
- **Unreadable `.toolignore`:** warning, built-ins only (the task says only an
  invalid root raises). Decoded as UTF-8 with replacement, BOM stripped.
- **Extensions** are matched case-sensitively (`.PY` is an other file).
- **CLI errors:** `cli.fail(message)` prints `error: <message>` to stderr and exits 1;
  `cli.require_dir` and `cli.load_or_fail` build on it for later commands. Plain
  `typer.echo` (not rich) for errors, so `[llm]` in messages is not eaten as rich
  markup. Typer pretty exceptions disabled.
- **`config show`** prints header comments (root, source file or "defaults",
  effective model, effective context_tokens), then `render_toml`. `render_toml`
  writes unset keys as `# <key> is not set` comments (TOML has no null), keeps
  model field order (fixed, so deterministic), and round-trips through `tomllib`.
- **Logging:** no handler is configured in this task. Library warnings reach stderr
  through Python's last-resort handler. Progress/log formatting is task 08.
- **Test infrastructure:** `pythonpath = ["."]` in pytest config so tests import
  `tests.helpers` / `tests.fixtures.builders`. `addopts = "--strict-markers"`.
  `tests/helpers.py` also has `snapshot_tree` (used for every no-write check).
  `run_cli` falls back to `CliRunner(mix_stderr=False)` for older click.
  `fixed_clock()` returns a constant aware datetime (placeholder for task 05).
- **`.gitignore`:** `build/`, `dist/`, `local/` were unanchored and would have
  swallowed later committed fixture folders of those names. Anchored to `/build/`,
  `/dist/`, `/local/`.

### Commands run

- `.venv/bin/python -m pip install -e ".[dev]"`: OK.
- `.venv/bin/python -m pytest -q`: **120 passed** (Python 3.14.7).
- `.venv/bin/python -m pytest -q -m live_llm`: 120 deselected (no live tests yet).
  The skip hook was checked with a throwaway `@pytest.mark.live_llm` test (since
  deleted): skipped by default, run under `-m live_llm`.
- Python 3.11.11 (`uv venv --python 3.11`, `uv pip install -e ".[dev]"`):
  **120 passed**. This covers the `TOMLDecodeError` path without `lineno`.
- Fresh pip venv in scratch space, `pip install -e ".[dev]"`, `codewhy --version` →
  `codewhy 0.1.0`.
- `uv build --wheel` (into scratch space, no new project dependency): wheel contains
  `codewhy/templates/codewhy.toml` and `codewhy/templates/toolignore`.
- Mutation checks on `ignore.py` (each reverted): disabling directory pruning fails
  the pruning and negation tests; dropping the always-excluded file check fails 6
  tests incl. self-pollution; following symlinks fails the symlink test.

### Manual verification

1. Fresh venv install + `codewhy --version` and `python -m codewhy --version`:
   **Passed**.
2. Scratch project (`main.py` listed in `.gitignore`, `app/util.py`,
   `web/index.js`, `node_modules/x/i.js`, invalid-UTF-8 `bad.py`, `README.md`):
   `init` created both files, a second `init` skipped both; `scan` printed
   `Found 3 source files in 3 modules (1 skipped, 2 other files)…` and
   `skipped bad.py (undecodable)` (`main.py` discovered despite `.gitignore`;
   `node_modules` ignored); `config show --provider gemini` showed gemini with
   effective context 32768 and "model not set"; `scan /nope` exited 1 with one
   error line: **Passed**.
3. Second OS: **Not run** (only macOS available; task 02's CI matrix covers it).
   Second Python version (3.11) run instead: Passed.

### Hardware / model-dependent checks

N/A (no LLM or network in this task).

### Deviations from the task

- None in scope. `README.md` is a stub as required. `snapshot_tree` added to
  `tests/helpers.py` (allowed file) for the no-write assertions.
- Wheel verification done with `uv build` rather than `python -m build`, so no `build`
  package was added to the dev extra.

### Open issues / for the project lead

- The architecture's `codewhy.toml` example shows `model` and `context_tokens`
  uncommented; the template comments them out (see Decisions). The lead may want
  the architecture's example adjusted to match; that has not been done.
- Case-insensitive filesystems: no issue observed; extensions are matched
  case-sensitively.
- Branch: work is on `main`, uncommitted, as instructed. `members/aditya.md`
  suggests `task/01-scaffold`.
