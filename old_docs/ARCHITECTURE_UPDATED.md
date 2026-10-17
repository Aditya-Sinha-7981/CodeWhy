# Local Codebase Documentation Generator — Architecture

## Purpose

A CLI tool that scans a local, possibly-ungitted folder of Python and/or
JS/TS files and produces a set of Markdown docs explaining what each file
does, why it exists, and how the pieces connect — deterministic extraction
feeding a hierarchical LLM summarization pass, with incremental re-runs via
content-hash caching. Full rationale and scope in `PROJECT_SPEC.md`; this
document is the "what goes where and why" for implementation.

## Locked principles (v1)

- Local folder in, Markdown files out. No hosted service, no DB server.
- Two languages only: Python (`ast`) and JS/TS (`tree-sitter`). No LLM-based
  structure guessing — Layer 1 is 100% deterministic.
- LLM calls are local-only via Ollama for v1. Provider interface stays
  pluggable in code, but no second provider is wired or tested in v1.
- Default model: `qwen2.5-coder:14b` (coding-tuned, fits comfortably in
  24GB RAM). Configurable via `codewhy.toml` — never hardcoded elsewhere.
- Caching is real from day one — this is what makes it a tool, not a script.
- Grounding/confidence (spec's Layer 5) is **out of v1 scope**. The
  interface seam is reserved (see below) but not implemented.
- No network calls except to localhost Ollama. No telemetry.

## Repo layout

```
codewhy/
├── pyproject.toml            # hatchling backend, single source of truth for deps
├── README.md
├── codewhy.toml.example       # copy-and-edit config template
├── src/
│   └── codewhy/
│       ├── __init__.py
│       ├── cli.py             # typer app, all subcommands
│       ├── config.py          # loads/validates codewhy.toml + .toolignore
│       ├── ignore.py          # built-in ignore list + .toolignore parsing
│       ├── extraction/
│       │   ├── python_ast.py
│       │   └── ts_treesitter.py
│       ├── graph.py           # networkx import graph builder
│       ├── cache/
│       │   ├── db.py          # sqlite3 connection + schema
│       │   └── invalidation.py # smart-cascade logic
│       ├── llm/
│       │   ├── provider.py    # abstract Provider interface
│       │   ├── ollama_provider.py
│       │   └── chunking.py    # AST-boundary chunking for oversized files
│       ├── summarize/
│       │   ├── file_pass.py
│       │   ├── module_pass.py
│       │   └── project_pass.py
│       └── render/
│           ├── docs_tree.py    # docs/ folder output
│           └── consolidated.py # single-file output
└── tests/
    ├── fixtures/               # small real-ish demo projects (see Build phases)
    └── ...
```

## Component architecture

```
User ── CLI (typer) ── config.py + ignore.py
                            │
                            ▼
                    Extraction (Layer 1)
              ┌─────────────┴─────────────┐
        python_ast.py              ts_treesitter.py
                            │
                            ▼
                  Dependency Graph (Layer 2, networkx)
                            │
                            ▼
                  Cache / Diff (Layer 3, sqlite3)
              (decides: which files need re-summarizing)
                            │
                            ▼
              Summarization (Layer 4, hierarchical)
        file_pass → module_pass → project_pass
              (each call routed through llm/provider.py
               → ollama_provider.py → local Ollama daemon)
                            │
                            ▼
                    Render (docs_tree.py or
                    consolidated.py, per config/flag)
                            │
                            ▼
                 docs/ folder or single .md file
```

## CLI surface

```
codewhy scan <path>            # full run: extract → graph → cache-diff → summarize → render
codewhy scan <path> --single-file        # force consolidated output regardless of size
codewhy scan <path> --docs-tree          # force docs/ folder output regardless of size
codewhy scan <path> --full               # ignore cache, regenerate everything
codewhy init <path>              # writes codewhy.toml.example → codewhy.toml, writes .toolignore
codewhy config show              # print resolved config (defaults + overrides)
```

`scan` is the only command that touches the pipeline. `init` and `config
show` are config convenience only — no separate "editor" tool for v1.

## Config & ignore files

**`codewhy.toml`** (project-root, optional — sane defaults if absent):

```toml
[llm]
provider = "ollama"
model = "qwen2.5-coder:14b"
host = "http://localhost:11434"

[output]
mode = "auto"            # "auto" | "single-file" | "docs-tree"
single_file_threshold = 5 # file count at/below which "auto" picks single-file

[extraction]
languages = ["python", "javascript", "typescript"]
full_source_line_limit = 150

[project]
# Optional explicit entry point. When set, this always overrides automatic detection.
# entry_point = "src/main.py"
```

**`.toolignore`** (project-root, optional): same syntax as `.gitignore`
(glob patterns, one per line). `.gitignore` itself is **never** read —
locked decision, avoids surprising a user whose `.gitignore` excludes
things they actually want documented (e.g. a `build/` folder they wrote
themselves and care about).

**Built-in default ignore list** (always applied, not overridable except by
the fact that `.toolignore` can't un-ignore these in v1 — flag if you want
an override escape hatch later): `node_modules/`, `.venv/`, `venv/`,
`__pycache__/`, `.git/`, `dist/`, `build/`, `.next/`, `*.min.js`.

## Module Definition

A **module** in v1 is any folder that directly contains at least one supported
source file (Python, JavaScript, or TypeScript), including the project root.

Module grouping is non-recursive: a module summary covers only supported source
files directly inside that folder. Files in subfolders belong to those
subfolders' modules.

Every qualifying folder gets exactly one `_module.md`, including folders with
only one supported source file. A folder containing only subfolders and no
supported source files gets no module document.

The project root is therefore always a module when it contains at least one
supported source file. Root-level source files belong to that root module.

## Entry Point Detection

Entry-point detection is deterministic and follows this fixed priority order;
the first valid match wins:

1. Explicit `entry_point` in `codewhy.toml`.
2. Package metadata:
   - `pyproject.toml`: `[project.scripts]` or `[tool.poetry.scripts]`.
   - `package.json`: `"main"`, then `"scripts.start"`.
   Metadata-derived targets are accepted only when they resolve to a file
   actually present in the scanned project.
3. Conventional filenames, in this exact order:
   `main.py`, `__main__.py`, `app.py`, `index.js`, `index.ts`,
   `src/main.py`, `src/index.js`, `src/index.ts`.
4. If nothing matches, the entry point is `None`.

When no entry point is detected, the project-level summarization prompt must
explicitly instruct the model to state that no conventional entry point was
detected. The LLM must not infer or guess an entry point from summaries.

`pyproject.toml` and `package.json` are therefore read structurally for
entry-point detection only; they are not otherwise supported source formats.

## Unsupported Files

Only Python, JavaScript, and TypeScript source files are parsed and
documented in v1.

All other files are excluded from per-file extraction and summarization.
`pyproject.toml` and `package.json` are narrow structural exceptions used only
for deterministic entry-point detection.

The project overview includes a deterministic **Other files in this project**
list containing paths of unsupported files. No interpretation is generated for
these files. README files receive no special treatment in v1.

## Layer 1 — Extraction

- `python_ast.py`: stdlib `ast`, walks the tree, emits per file: functions
  (name, signature, docstring), classes (name, methods, docstring), imports
  (module, names, is-relative), line count.
- `ts_treesitter.py`: prebuilt grammar wheels (no compiler needed), same
  output shape as the Python extractor — a shared `FileStructure` dataclass
  so Layer 2+ never branches on language.
- Never calls the LLM. Same input → same output, always. This is what
  makes cache-hashing meaningful.

## Layer 2 — Dependency graph

- `networkx` `DiGraph`, one node per file, edge `A → B` when A imports B.
- Only local/relative imports resolved into edges — third-party packages
  are recorded as metadata on the node, not graph edges.
- v1 scope: who-imports-whom only. No PageRank/importance ranking (that's
  the spec's named v2 nice-to-have).

## Layer 3 — Cache / smart invalidation

**SQLite schema** (`.codewhy/cache.db` inside the scanned project, add to
default ignore list so it never gets treated as source):

```sql
CREATE TABLE file_state (
  path TEXT PRIMARY KEY,
  content_hash TEXT NOT NULL,
  summary TEXT,
  summary_hash TEXT,          -- hash of the summary TEXT itself
  last_generated TIMESTAMP
);

CREATE TABLE module_state (
  module_path TEXT PRIMARY KEY,  -- folder path
  summary TEXT,
  summary_hash TEXT,
  member_summary_hashes TEXT,     -- JSON array, one per file in the module, in order
  last_generated TIMESTAMP
);

CREATE TABLE project_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton row
  summary TEXT,
  module_summary_hashes TEXT,      -- JSON array
  last_generated TIMESTAMP
);
```

**Smart cascade rule** (locked decision):

1. Diff `content_hash` per file against last run → set of changed files.
2. Re-run `file_pass` only for changed files → get new structured summary,
   compute its `summary_hash`.
3. Compute the current canonical graph representation and fingerprints.
4. For each module containing a changed file, or whose relevant graph
   fingerprint changed: compare both its stored `member_summary_hashes` and
   `graph_fingerprint` against the current values. Re-run `module_pass` if
   either differs.
5. At project level, compare both the stored `module_summary_hashes` and the
   full-project `graph_fingerprint` against current values. Re-run
   `project_pass` if either differs.
6. `--full` bypasses steps 1–5 entirely and regenerates everything.

A source change whose file summary remains identically worded therefore does
not cascade upward unless the relevant dependency graph also changed.

Deterministic output sections are always rendered from the current extraction
and graph state on every run. Cache state gates only expensive LLM prose
generation; it never causes deterministic dependency information to remain
stale.

### Graph fingerprint canonicalization

A graph fingerprint is computed from a canonical, sorted representation of
the relevant directed edges. Each edge is represented using normalized
project-relative source and destination file paths as:

`<source_relative_path> -> <destination_relative_path>`

Paths use `/` separators and are sorted lexicographically before hashing.

A module's `graph_fingerprint` includes all graph edges where either endpoint
belongs to that module, so both outgoing and incoming relationships relevant
to the module are covered. The project `graph_fingerprint` includes every
resolved local edge in the project.

Thus a change such as `A.py -> B.py` becoming `A.py -> C.py` changes the
relevant fingerprint even if the LLM produces the same file-level summary.


## Layer 4 — Summarization (hierarchical, map-reduce)

### Documentation contract

Generated Markdown follows a fixed renderer-controlled schema. LLM output is
structured JSON for each pass, never free-form Markdown. The renderer combines
LLM prose with deterministic extraction/graph data.

**File documentation:**

- `Purpose` — LLM, mandatory.
- `Depends On` — deterministic graph data, mandatory.
- `Used By` — deterministic reverse graph edges, mandatory.
- `Defined Here` — deterministic Layer 1 data (functions, classes, methods,
  signatures), mandatory.
- `Notes` — LLM, optional and omitted when empty.

**Module documentation (`_module.md`):**

- `Files in this module` — deterministic.
- `Internal dependencies` — deterministic graph data.
- `External dependencies` — deterministic graph data.
- `Responsibility` — LLM.
- `How the pieces cooperate` — LLM.

**Project overview (`PROJECT_OVERVIEW.md`):**

- `Entry Point` — deterministic entry-point detection.
- `Modules` — deterministic module list.
- `Other files in this project` — deterministic paths of unsupported files.
- `Overview` — LLM.
- `Architecture` — LLM.

Structural facts are never delegated to the LLM when they can be rendered from
Layer 1 or Layer 2. The LLM is responsible for explanation and interpretation,
primarily answering "why" rather than restating known structural facts.

LLM prompts must instruct:

> Do not invent behavior, APIs, or functionality not evidenced in the provided
> structure or source. If intent cannot be confidently established, say so
> plainly rather than fabricating a plausible explanation.

This is a v1 reliability rule, not the deferred Layer 5 grounding/confidence
system.

- **Provider interface** (`llm/provider.py`): abstract `summarize(prompt:
  str) -> str`. `ollama_provider.py` is the only implementation in v1, but
  nothing in `summarize/` imports it directly — always through the
  interface, so a second provider is a new file, not a refactor.
- **`file_pass.py`**: prompts with Layer 1's extracted structure always;
  includes full source additionally when the file is under a configurable
  line threshold (default 150 lines — add to `codewhy.toml` under
  `[extraction]` as `full_source_line_limit` if you want it tunable).
- **Chunking for oversized files** (locked decision): when a file exceeds
  the model's usable context, split along the function/class boundaries
  already identified in Layer 1 — never naive fixed-token chunking. Each
  chunk gets summarized independently, then those chunk-summaries are
  combined in one final call into a single file-level summary. This reuses
  Layer 1's output for free and never cuts a function in half.
- **`module_pass.py`**: groups according to the Module Definition above
  (folder-based, non-recursive), feeds file-level summaries + Layer 2's
  relevant edges, and returns structured fields for `Responsibility` and
  `How the pieces cooperate`.
- **`project_pass.py`**: feeds all module summaries plus deterministic
  entry-point, module, and unsupported-file information, and returns
  structured fields for `Overview` and `Architecture`. It must not infer an
  entry point when deterministic detection returned `None`.

## Layer 5 — Grounding (reserved, not implemented in v1)

Interface seam only: `summarize/` functions return `(summary: str,
metadata: dict)` instead of a bare string, with `metadata` currently
always `{}`. This costs nothing now and means a future confidence-flagging
pass slots into the existing return shape instead of requiring a signature
change across every call site. Document this explicitly in the README as
"not implemented — reserved for v2."

## Output

**docs-tree mode** — `docs/` folder mirroring project structure:
- One `.md` per supported source file, named `<original-filename>.md`
- One `.md` per qualifying module/folder, named `_module.md` inside that
  folder's mirrored location under `docs/`
- The project root gets `docs/_module.md` when it contains supported source
  files, because the root is a module under the v1 module rule.
- One `docs/PROJECT_OVERVIEW.md` at the root

**Every generated file's frontmatter:**
```markdown
---
generated_by: codewhy v0.1.0
source: <relative path to the original file, or "project" for the overview>
generated_at: <ISO 8601 timestamp>
---
```
(Stated assumption — flag if you want a different freshness marker.)

**single-file mode**: same content, concatenated into one
`PROJECT_DOCS.md` with the project overview first, then module summaries,
then file summaries — no separate frontmatter per section, one header
block at the top instead.

**Mode selection** (locked decision): `output.mode = "auto"` in config —
if total source file count ≤ `single_file_threshold` (default 5),
single-file; otherwise docs-tree. `--single-file` / `--docs-tree` CLI
flags always override config, in either direction.

## Failure boundaries

| Failure | Handling |
|---|---|
| Ollama not running / unreachable | Fail fast before any extraction work, clear message with the fix (`ollama serve`) |
| Requested model not pulled locally | Fail fast, clear message with the fix (`ollama pull qwen2.5-coder:14b`) |
| tree-sitter grammar missing for a found `.ts`/`.js` file | Skip that file, log a warning, continue — don't fail the whole run over one file |
| Malformed Python (`ast` parse error) | Same as above — skip, warn, continue |
| SQLite cache file corrupted/unreadable | Treat as cache-empty (full regenerate), log a warning, don't crash |
| LLM returns malformed/unusable output for a pass | Retry once; on second failure, mark that file/module/project summary as `[generation failed]` in output rather than aborting the whole run |

## Build phases

1. **Extraction + graph** — `python_ast.py`, `ts_treesitter.py`,
   `graph.py`. No LLM, no cache yet. Demoable: run on 2–3 real local
   projects (including your own messier ones), print the extracted
   structure and graph as JSON. This is also where `tests/fixtures/` gets
   built — keep 2–3 small fixture projects around for every later phase.
2. **MVP narration** — `file_pass.py` only, no caching, no chunking, no
   hierarchy. Ugly but end-to-end: folder in, flat per-file `.md` out.
3. **Hierarchical rollup + real caching** — `module_pass.py`,
   `project_pass.py`, SQLite schema, smart-cascade logic, both output
   modes (`docs_tree.py`, `consolidated.py`).
4. **Chunking + polish on Layer 4** — AST-boundary chunking for oversized
   files, `codewhy.toml`/`.toolignore` support, `init`/`config show`
   commands, failure-boundary handling per the table above.
5. **Packaging + demo prep** — `pyproject.toml` + hatchling, verify
   `pipx install` and `uvx` both work end-to-end, README, freeze the
   fixture demo repos you'll show in your submission.

## Locked implementation decisions

- `full_source_line_limit` default: 150 lines.
- `single_file_threshold` default: 5 files.
- Default model: `qwen2.5-coder:14b`.
- Cache DB lives at `.codewhy/cache.db` inside the scanned project.
- `src/` package layout over flat layout.
- Module definition is folder-based, non-recursive, with the project root
  treated as a module when it directly contains supported source files.
- Entry-point detection uses the fixed deterministic priority defined above.
- Unsupported files are not parsed or summarized; their paths appear only in
  the deterministic project-overview "Other files" list.
- Generated documentation uses the fixed deterministic/LLM schema defined
  above.
- LLM passes return structured JSON matching their fixed pass schema; the
  renderer owns the Markdown format.
- Graph changes participate in cache invalidation through canonical graph
  fingerprints.
- Deterministic dependency/output sections are re-rendered from current
  extraction/graph state on every run.
- Frontmatter freshness marker as shown above, no alternative considered.

## Explicitly deferred (not v1)

- Final project name
- Second LLM provider (Gemini or otherwise)
- Layer 5 grounding/confidence logic (seam reserved only)
- Graph-clustering-based module grouping (PageRank-style importance)
- `.toolignore` override of the built-in default ignore list
- README-aware project understanding
- richer support for non-source project metadata beyond entry-point detection
