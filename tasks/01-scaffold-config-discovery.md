# codewhy Task Specification

> **This file is a work order for the assigned developer and their AI coding agent.**
>
> This task defines the scope of the assigned work. Do not expand the scope without explicit approval from the project lead.

---

## Task Information

**Task:** `Project scaffold, config, file discovery, module model`

**Task ID:** `CW-01` (index: `tasks/ALL-TASKS.md`)

**Depends on:** nothing

**Branch:** `task/01-scaffold` (suggested; create only when the user wants a branch)

**Priority:** `Critical` (everything imports from this)

**Status:** `Not Started`

**Work log:** `logs/01-scaffold-config-discovery.md`

---

# 1. Goal

## Objective

Create the installable project skeleton and the first deterministic layer: loading configuration, deciding which files are in play, and grouping them into modules. Deliverables:

1. **An installable package.** `pip install -e ".[dev]"` works; `codewhy --version` and `python -m codewhy --version` work.
2. **Config.** `codewhy.toml` loading with strict validation and CLI-override layering, per `docs/ARCHITECTURE.md` → *Config, ignore, and discovery*.
3. **Discovery.** Built-in ignores, `.toolignore`, always-excluded artifacts, symlink/size/decoding guards, language mapping, deterministic ordering.
4. **Project model.** Modules per the *Module Definition*, plus the deterministic list of unsupported ("other") files.
5. **Three commands:** `codewhy init`, `codewhy config show`, and a `scan` stub that runs discovery and reports what it found.
6. **Test infrastructure** used by every later task: `make_project`, `run_cli`, the fixture-builder module, pytest markers.

No parsing, no LLM, no graph. Those are later tasks.

## Why This Exists

Every later layer consumes `ProjectModel`. If discovery is wrong (documents its own output, follows a symlink loop, reads `.gitignore`, hashes CRLF differently on Windows), the error is invisible until the cache or the output looks wrong three tasks later. The test helpers and path/newline conventions fixed here are what make the rest of the suite portable and deterministic.

---

# 2. Authoritative Documentation

* `CLAUDE.md` — conventions (POSIX-relative paths, newline normalization, determinism, no `print` in library code)
* `docs/ARCHITECTURE.md` — *Product goals*, *Locked principles*, *Repo layout*, *CLI surface*, *Config, ignore, and discovery*, *Module Definition*, *Unsupported Files*, *Failure boundaries*, *Locked implementation decisions*
* `docs/TESTS.md` — *Helpers*, *Fixture projects*, *Discovery and ignore*, *Module definition and project model*, *CLI* (`init`, `config show`)

If this task and the architecture disagree, record it in the work log and ask; do not choose silently.

---

# 3. Scope

## In Scope

The implementation MUST include:

* `pyproject.toml` (hatchling, `src/` layout, console script `codewhy`, `requires-python >=3.11`, runtime deps **typer, rich, pathspec, pydantic>=2**, dev extra **pytest, pytest-mock**, pytest config with a registered `live_llm` marker).
* `src/codewhy/` package: `__init__.py` (`__version__`), `__main__.py`, `cli.py`, `config.py`, `ignore.py`, `project.py`, `templates/`.
* Strict config models for **every** key in the architecture's config (including `[llm]` keys not yet used), with defaults and validation.
* Discovery returning typed results (supported files with text, skips with reasons, other files).
* `ProjectModel` with modules sorted by path.
* `init`, `config show`, and the `scan` stub.
* `codewhy.toml.example` at the repo root, byte-identical to `src/codewhy/templates/codewhy.toml` (enforced by a test).
* Tests listed in §8, a stub `README.md`, `.gitignore`, and the work log.

## Out of Scope

The implementation MUST NOT include:

* Any parsing of file contents beyond decoding to text (task 02).
* Import resolution, graph, entry-point detection (task 03).
* Any provider/LLM/network code, `httpx`, `networkx`, `tree-sitter` (tasks 02–04).
* `inspect`, `doctor`, `--dry-run`, progress output (later tasks).
* Applying provider presets (`gemini`) — the config model only *accepts* the keys; preset resolution is task 04.
* Reading `.gitignore` in any way.

Do not implement "while we're here" improvements outside this scope.

---

# 4. Allowed Files / Areas

```text
pyproject.toml
README.md                                   (stub: name, one-line purpose, "under construction")
.gitignore
codewhy.toml.example
src/codewhy/__init__.py
src/codewhy/__main__.py
src/codewhy/cli.py
src/codewhy/config.py
src/codewhy/ignore.py
src/codewhy/project.py
src/codewhy/templates/codewhy.toml
src/codewhy/templates/toolignore
tests/conftest.py
tests/helpers.py                            (make_project, run_cli, fixed_clock placeholder)
tests/fixtures/__init__.py
tests/fixtures/builders.py                  (build_ignored_content now; others added by later tasks)
tests/test_config.py
tests/test_ignore.py
tests/test_project.py
tests/test_cli_init_config.py
logs/01-scaffold-config-discovery.md
```

### May Modify If Required

```text
docs/ARCHITECTURE.md   (only if implementation forces a contract change — say so in the log)
docs/TESTS.md          (only to correct a statement this task proves wrong)
```

---

# 5. Dependencies

| Dependency | Type | Notes |
|---|---|---|
| Python 3.11+ | Environment | `tomllib` is required. |
| `typer`, `rich`, `pathspec`, `pydantic>=2` | Runtime | Locked list in the architecture. No others. |
| `pytest`, `pytest-mock` | Dev | Dev extra only. |

### Dependency Status

* [x] Dependency exists and is ready
* [ ] Dependency is being developed separately
* [ ] Dependency requires coordination
* [x] No external dependency beyond the packages above

---

# 6. Contracts That Must Be Preserved

### Architecture

* Config keys, defaults, and section names exactly as in `docs/ARCHITECTURE.md` → *Config*. Provider-specific `context_tokens` defaults: `ollama` 16384, `openai-compat`/`gemini` 32768.
* `.gitignore` is **never** read. `.toolignore` uses `.gitignore` syntax, parsed with `pathspec`. Built-in patterns come **before** `.toolignore` patterns in one ordered set so `!negation` can re-include them.
* Always excluded and not overridable by `.toolignore`: `.codewhy/`, the configured output `dir`, the configured `single_file_name` (at root), `codewhy.toml`, `.toolignore` (at root).
* Module = folder directly containing ≥1 supported file (root included, path `.`); non-recursive; modules ordered by path.

### Data conventions

* Every path in every returned structure is a project-relative POSIX string. Text has `\r\n` → `\n`. A UTF-8 BOM is tolerated and stripped.

### Local-first

* Nothing in this task touches the network.

### Safety

* Symlinks are never followed. `init` never overwrites an existing file. `config show`, `scan` (stub) write nothing.

---

# 7. Implementation Requirements

### Requirement 1 — Package scaffold

* `pyproject.toml`: project name `codewhy`, version `0.1.0`, console script `codewhy = "codewhy.cli:app"`, hatchling build with the `src/` layout, templates included in the wheel (verify with `python -m build --wheel` and listing the wheel, or document why this is deferred to task 09). Pytest: `testpaths = ["tests"]`, markers `live_llm` (skipped by default via a `conftest.py` hook unless `-m live_llm` is given).
* `__init__.py` exposes `__version__` (single source: read from package metadata or a constant kept in sync — pick one, log it).
* `codewhy --version` prints `codewhy 0.1.0`.
* `.gitignore`: Python artifacts, `.venv/`, `dist/`, `build/`, `.codewhy/`, `__pycache__/`, `.pytest_cache/`. **Trap:** these patterns must not break committing `tests/fixtures/` directories created in later tasks; do not ignore `tests/fixtures/**` content by pattern accident.

### Requirement 2 — Config (`config.py`)

* Pydantic v2 models, `extra="forbid"` everywhere. Sections: `llm`, `output`, `extraction`, `project`. Include every key from the architecture config: `llm.provider` (`ollama|openai-compat|gemini`), `model`, `host`, `base_url`, `api_key_env`, `context_tokens`, `max_output_tokens`, `temperature`, `seed`, `timeout_seconds`, `max_transient_retries`, `structured_output` (`schema|json|prompt`), `send_seed`, `reasoning_effort`, `acknowledge_remote`; `output.mode` (`single-file|docs-tree|both`), `dir`, `single_file_name`, `token_budget`; `extraction.languages` (subset of `python|javascript|typescript`), `max_file_bytes`; `project.entry_point`.
* Defaults match the architecture. `Config.llm.effective_context_tokens` returns the explicit value or the provider-specific default.
* Validation: positive ints where required, `0 <= temperature <= 2`, `output.dir` and `single_file_name` must be relative, not contain `..`, and not collide with each other; `project.entry_point` must be a relative POSIX path (existence is checked later, in task 03).
* `load_config(root: Path) -> RawConfig` reads `codewhy.toml` if present (`tomllib`). Unknown keys fail with a message naming the section, key, and a `difflib.get_close_matches` suggestion (`unknown key [llm].modle (did you mean "model"?)`). Invalid TOML fails with file, line, and column from the `tomllib` error. All problems are collected and reported together in one `ConfigError`.
* `resolve_config(root: Path, overrides: dict[str, object]) -> Config` layers: defaults < `codewhy.toml` < overrides (dotted keys like `"llm.provider"`; `None` values mean "not provided"). The result records `source_path` (the TOML file used, or `None`).
* `render_toml(config) -> str`: a minimal TOML serializer for scalars, lists of strings, and tables (no `tomli_w`). Used by `config show`.

### Requirement 3 — Discovery (`ignore.py`)

```python
@dataclass(frozen=True)
class SourceFile:  path: str; language: str; text: str; size: int
@dataclass(frozen=True)
class Skip:        path: str; reason: Literal["too_large", "undecodable", "unreadable"]; detail: str
@dataclass(frozen=True)
class DiscoveryResult: files: list[SourceFile]; skips: list[Skip]; other_files: list[str]
def discover(root: Path, config: Config) -> DiscoveryResult
```

* Built-in ignore list and always-excluded set exactly as in the architecture; `.toolignore` read as UTF-8, blank/comment lines per gitignore rules, an invalid pattern is a warning (via `logging`), not a crash. Use `pathspec`'s gitignore-style spec (prefer `GitIgnoreSpec` if the installed version provides it; log which you used).
* Walk: sort directory entries by name for determinism; prune ignored directories (test the directory path with a trailing `/`); never descend into them. Never follow symlinks (neither files nor directories); symlinks are skipped silently (log at debug level) and are **not** recorded as `Skip`s.
* Negation caveat to implement and test: `!build/` re-includes the directory; `!build/keep.py` **cannot** re-include a file inside an ignored directory (same as git). Document this in a code comment and in the work log.
* Language by extension: Python `.py`; JavaScript `.js .jsx .mjs .cjs`; TypeScript `.ts .tsx .mts .cts`; filtered by `config.extraction.languages`. `*.d.ts` is a built-in ignore. A supported extension for a disabled language counts as an **other file**.
* Guards: size > `max_file_bytes` → `Skip(too_large)`; bytes that fail UTF-8 decoding (after BOM handling) → `Skip(undecodable)`; `OSError` while reading → `Skip(unreadable)`. A skip never stops discovery.
* Text normalization: `\r\n` → `\n` (and lone `\r` → `\n`). `size` is the on-disk byte size.
* `other_files`: every non-ignored, non-always-excluded file whose extension is not a supported one, as sorted POSIX paths. (The 100-entry cap is applied at render time, not here.)
* `root` must be an existing directory, else `DiscoveryError` with a clear message.

### Requirement 4 — Project model (`project.py`)

```python
@dataclass(frozen=True)
class Module: path: str; files: list[str]      # "." for the root module; files sorted
@dataclass(frozen=True)
class ProjectModel: root: Path; name: str; files: dict[str, SourceFile]; modules: list[Module]; other_files: list[str]; skips: list[Skip]
def build_project(root: Path, config: Config) -> ProjectModel
```

* `name` is the root folder's name (resolve `.` to the real directory name).
* Modules per the *Module Definition*: group each supported file under its directory; a directory with only subfolders yields no module; modules sorted by path with `.` first.
* No entry point yet (task 03 adds it).

### Requirement 5 — CLI (`cli.py`)

* `typer` app named `app`. Global `--version`.
* `codewhy init [PATH]` (default `.`): writes `codewhy.toml` from the packaged template and `.toolignore` from the packaged template, each only if absent; prints `created <file>` / `skipped <file> (already exists)`; exit 0. Template contents: the full commented config from the architecture (every key present as a commented or default line, `[project] entry_point` commented), and a `.toolignore` that explains the syntax, mentions that built-ins always apply, shows `!build/` as a commented example, and states that `.gitignore` is never read.
* `codewhy config show [PATH]`: resolves config (reading `codewhy.toml` if present) and prints a header comment naming the root and the source file (or "defaults"), then `render_toml` output. `ConfigError` → message on stderr, exit 1. Accept `--provider` and `--model` as overrides now (stored, not interpreted).
* `codewhy scan [PATH]` (stub): resolve config, `build_project`, print `Found N source files in M modules (K skipped, L other files). The documentation pipeline is not implemented yet.` plus one `skipped <path> (<reason>)` line per skip; exit 0. Do not write any file. Invalid PATH → exit 1 with a clear message.
* Errors print one clear line to stderr (no tracebacks) and exit 1; use a small helper so later tasks reuse it.

### Requirement 6 — Test infrastructure

* `tests/helpers.py`: `make_project(tmp_path, {"main.py": "...", "pkg/__init__.py": ""})` (creates parents, writes text, returns the root), `run_cli(args, cwd=None)` wrapping `typer.testing.CliRunner` and returning exit code, stdout, stderr.
* `tests/fixtures/builders.py`: `build_ignored_content(tmp_path)` creating `node_modules/`, `.venv/`, a custom `.toolignore`, a `.gitignore` that lists a file the user wants documented, a user-owned `build/` re-included via `!build/`, and a few healthy source files. (It is built in code because the repo's own `.gitignore` would swallow these directories.)

### Error Handling

* Config problems: one `ConfigError` listing all issues; never a traceback.
* Discovery problems: skips are data, not exceptions. Only an invalid root raises.
* If a behavior in the architecture is ambiguous while implementing (for example how `pathspec` treats a particular negation), write the exact behavior you observed into the work log and test it so it is pinned.

---

# 8. Testing Requirements

## Automated Tests

* `test_config.py`: defaults match the architecture; `effective_context_tokens` per provider; unknown-key suggestion text; invalid TOML location reported; multiple errors reported together; each validation rule (temperature, relative paths, collisions); override layering and `None` overrides; `render_toml` round-trips through `tomllib` to an equal config.
* `test_ignore.py` (all against `make_project` or `build_ignored_content`): built-ins applied with no `.toolignore`; `.toolignore` extends; `!build/` re-includes; `!build/keep.py` does not; `.gitignore` has zero effect (a file listed there is still discovered); always-excluded artifacts never appear as source or in `other_files`, even when `.toolignore` tries to negate them; **self-pollution regression** (discover, write fake output files and a cache dir into the tree, discover again → identical result); pruning (a deliberately unreadable directory inside `node_modules/` does not break discovery); symlinks not followed (create a symlink loop and a link to outside the root where the platform allows; otherwise `pytest.skip` with a reason); `max_file_bytes` skip; undecodable skip; BOM stripped; CRLF normalized; extension mapping for every extension incl. `.mjs .cjs .mts .cts`; `*.d.ts` ignored; disabled language → other file; deterministic ordering (reverse creation order gives identical results).
* `test_project.py`: module definition cases from `docs/TESTS.md` (folder with only subfolders → no module; root module `.`; non-recursive; single-file folder still a module); `name` is the real folder name for `.`.
* `test_cli_init_config.py`: `init` creates both files, second run skips both and says so, never clobbers (content unchanged); `config show` reflects a real `codewhy.toml` and overrides; bad config → exit 1 with the message; `scan` stub reports counts and skips, writes nothing (snapshot the tree before/after); `--version`.
* A test that `codewhy.toml.example` is byte-identical to the packaged template.

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected result:

```text
all tests pass; no network access; no test depends on the order of files created
```

## Hardware / Model-Dependent Checks

None. Record "N/A" in the work log.

## Manual Verification

1. `pip install -e ".[dev]"` in a fresh venv; run `codewhy --version`.
2. Make a scratch folder with a few `.py`/`.js` files plus a `node_modules/` and a `.gitignore`; run `codewhy init`, `codewhy config show`, `codewhy scan` and read the output for sanity.
3. Run the test suite on a second OS if available (otherwise note "not run" — task 02's CI check will cover it).

---

# 9. Acceptance Criteria

* [ ] `pip install -e ".[dev]"` works from a clean venv; `codewhy --version` works.
* [ ] Config loads, validates strictly, layers overrides, and reports all errors at once.
* [ ] Discovery obeys every rule in §7 Requirement 3; `.gitignore` is never read.
* [ ] `ProjectModel` matches the Module Definition on the test projects.
* [ ] `init` never overwrites; `config show` and `scan` stub write nothing.
* [ ] `codewhy.toml.example` equals the packaged template.
* [ ] All tests pass offline.
* [ ] No dependencies beyond the listed ones.
* [ ] No source file contents appear in logs; no `print` in library modules.
* [ ] `git diff` reviewed; work log updated.
* [ ] If a commit was authorized: staged diff reviewed, only intended files, no AI attribution, no push/PR.

---

# 10. Known Risks

* **`pathspec` negation semantics** differ subtly from git in some versions. Pin the observed behavior in tests rather than trusting memory.
* **Windows:** path separators, symlink permissions (tests must skip, not fail), and CRLF are the usual breakages. Keep all internal paths POSIX and convert only at the filesystem boundary.
* **Pruned-directory trap:** testing `build` without the trailing slash will not match a `build/` pattern; testing only files will make pruning ineffective and slow on large `node_modules`.
* **Case-insensitive filesystems** (macOS default, Windows): `Main.py` vs `main.py` may be the same file. Do not try to be clever; record behavior if you observe an issue.
* The `.gitignore` for this repo itself can hide committed fixtures later (`node_modules/`, `.venv/`). That is why those fixtures are built in code.

---

# 11. Open Questions

* **requires clarification:** pip+venv or `uv` for the documented dev workflow? — Recommended: document pip+venv; `uv` works unchanged and may be mentioned as optional.
* **requires clarification:** Version source of truth (constant vs package metadata)? — Recommended: a constant in `__init__.py`, with the build reading it dynamically; log the choice.

---

# 12. Handoff Notes

* Task 02 consumes `SourceFile` (text, language) and extends `tests/fixtures/builders.py` with `build_broken_files`.
* Task 03 extends `ProjectModel` (entry point, graph) and reads `config.project.entry_point`.
* Task 04 applies provider presets on top of the config model defined here and adds `--provider`/`--model` handling.
* Record in the log: the version-source decision, the `pathspec` class used, pinned negation behavior, and any architecture edits.

If the task is complete:

> No outstanding handoff notes.

---

# 13. Definition of Done

See `CLAUDE.md` → *Definition of done*. Do not commit, push, open a PR, or merge unless the user explicitly authorizes that specific action.

## Final Agent Instruction

Before making changes: read `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/TESTS.md`, and this file completely; run `git status`; write your plan into `logs/01-scaffold-config-discovery.md`.

During implementation: stay in scope; do not invent architecture; keep determinism and POSIX-path conventions; test incrementally.

If the task requires something not covered here (a dependency, a contract change, new user-visible behavior), stop and ask the project lead.

Before handing off: run the tests, review `git diff` and `git status`, update the log, and report what changed, what was verified, and what is untested or unresolved.
