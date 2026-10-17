# Local Codebase Documentation Generator — Architecture

Working placeholder name used throughout this doc: `codedoc`. Swap for the real
name everywhere (package name, CLI entry point, repo) once chosen — nothing
else changes.

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
  24GB RAM). Configurable via `codedoc.toml` — never hardcoded elsewhere.
- Caching is real from day one — this is what makes it a tool, not a script.
- Grounding/confidence (spec's Layer 5) is **out of v1 scope**. The
  interface seam is reserved (see below) but not implemented.
- No network calls except to localhost Ollama. No telemetry.

## Repo layout

```
codedoc/
├── pyproject.toml            # hatchling backend, single source of truth for deps
├── README.md
├── codedoc.toml.example       # copy-and-edit config template
├── src/
│   └── codedoc/
│       ├── __init__.py
│       ├── cli.py             # typer app, all subcommands
│       ├── config.py          # loads/validates codedoc.toml + .toolignore
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
codedoc scan <path>            # full run: extract → graph → cache-diff → summarize → render
codedoc scan <path> --single-file        # force consolidated output regardless of size
codedoc scan <path> --docs-tree          # force docs/ folder output regardless of size
codedoc scan <path> --full               # ignore cache, regenerate everything
codedoc init <path>              # writes codedoc.toml.example → codedoc.toml, writes .toolignore
codedoc config show              # print resolved config (defaults + overrides)
```

`scan` is the only command that touches the pipeline. `init` and `config
show` are config convenience only — no separate "editor" tool for v1.

## Config & ignore files

**`codedoc.toml`** (project-root, optional — sane defaults if absent):

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

**SQLite schema** (`.codedoc/cache.db` inside the scanned project, add to
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
2. Re-run `file_pass` only for changed files → get new `summary`, compute
   new `summary_hash`.
3. For each module containing a changed file: compare its stored
   `member_summary_hashes` against the current set. **Only** re-run
   `module_pass` if that comparison differs — a file whose source changed
   but whose *summary came out identically worded* does not cascade up.
4. Same comparison one level higher: re-run `project_pass` only if the set
   of `module_summary_hashes` differs from what's stored.
5. `--full` bypasses steps 1–4 entirely and regenerates everything.

This means a docstring typo fix that doesn't change the LLM's summary
wording costs one file-level call and stops there — the expensive
project-level rollup only re-runs when something meaningfully changed.

## Layer 4 — Summarization (hierarchical, map-reduce)

- **Provider interface** (`llm/provider.py`): abstract `summarize(prompt:
  str) -> str`. `ollama_provider.py` is the only implementation in v1, but
  nothing in `summarize/` imports it directly — always through the
  interface, so a second provider is a new file, not a refactor.
- **`file_pass.py`**: prompts with Layer 1's extracted structure always;
  includes full source additionally when the file is under a configurable
  line threshold (default 150 lines — add to `codedoc.toml` under
  `[extraction]` as `full_source_line_limit` if you want it tunable).
- **Chunking for oversized files** (locked decision): when a file exceeds
  the model's usable context, split along the function/class boundaries
  already identified in Layer 1 — never naive fixed-token chunking. Each
  chunk gets summarized independently, then those chunk-summaries are
  combined in one final call into a single file-level summary. This reuses
  Layer 1's output for free and never cuts a function in half.
- **`module_pass.py`**: groups by folder (v1 — no graph clustering yet),
  feeds file-level summaries + Layer 2's edges between them, asks what the
  folder is responsible for and how its pieces cooperate.
- **`project_pass.py`**: feeds all module summaries, asks for the
  top-level overview, entry point, and overall data/control flow.

## Layer 5 — Grounding (reserved, not implemented in v1)

Interface seam only: `summarize/` functions return `(summary: str,
metadata: dict)` instead of a bare string, with `metadata` currently
always `{}`. This costs nothing now and means a future confidence-flagging
pass slots into the existing return shape instead of requiring a signature
change across every call site. Document this explicitly in the README as
"not implemented — reserved for v2."

## Output

**docs-tree mode** — `docs/` folder mirroring project structure:
- One `.md` per source file, named `<original-filename>.md`
- One `.md` per folder/module, named `_module.md` inside that folder's
  mirrored location under `docs/`
- One `docs/PROJECT_OVERVIEW.md` at the root

**Every generated file's frontmatter:**
```markdown
---
generated_by: codedoc v0.1.0
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
   files, `codedoc.toml`/`.toolignore` support, `init`/`config show`
   commands, failure-boundary handling per the table above.
5. **Packaging + demo prep** — `pyproject.toml` + hatchling, verify
   `pipx install` and `uvx` both work end-to-end, README, freeze the
   fixture demo repos you'll show in your submission.

## Stated assumptions (flag any you want changed)

- `full_source_line_limit` default: 150 lines
- `single_file_threshold` default: 5 files
- Default model: `qwen2.5-coder:14b`
- Cache DB lives at `.codedoc/cache.db` inside the scanned project
- `src/` package layout over flat layout
- Frontmatter freshness marker as shown above, no alternative considered

## Explicitly deferred (not v1)

- Final project name
- Second LLM provider (Gemini or otherwise)
- Layer 5 grounding/confidence logic (seam reserved only)
- Graph-clustering-based module grouping (PageRank-style importance)
- `.toolignore` override of the built-in default ignore list
