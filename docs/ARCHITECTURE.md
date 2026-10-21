# codewhy — Architecture (v1)

> **Source of truth for implementation.** `PROJECT_SPEC.md` is background
> rationale only; where the two disagree, this document wins. `TESTS.md` is
> the test plan for this document. If implementation forces a change here,
> change this file in the same commit.

## Purpose

`codewhy` is a CLI that scans a local, possibly-ungitted folder of Python
and/or JS/TS files and writes Markdown explaining what each file does, why it
exists, and how the pieces connect. Deterministic extraction feeds a
hierarchical LLM summarization pass; incremental re-runs are driven by a
content-addressed cache.

The intended experience:

```
cd my-project
uvx codewhy scan .
# -> PROJECT_DOCS.md  (paste it into any chat, no agentic tools needed)
```

## Product goals (priority order — these break ties)

1. **One command.** `uvx codewhy scan .` works in any folder: no git, no
   config file, no per-project setup. The only prerequisite is an LLM the user
   can reach (local Ollama or a hosted API).
2. **The primary output is one Markdown file small enough to paste into a
   chat window**, and the tool tells the user how big it is.
3. **Facts come from code; prose comes from the model.** Anything derivable
   from the AST or the import graph is rendered deterministically and never
   delegated to the LLM.
4. **Re-running is cheap.** Unchanged code costs zero LLM calls and produces
   byte-identical output.
5. **No surprises.** No writes outside the scanned folder, no overwriting
   files the tool did not create, no source code leaving the machine without
   explicit acknowledgement.

## Locked principles (v1)

- Local folder in, Markdown out. No hosted service, no DB server, no git.
- Two languages only: Python (`ast`) and JS/TS (`tree-sitter`). No LLM-based
  structure guessing — Layer 1 is 100% deterministic.
- LLM access goes through a `Provider` interface with **two** v1
  implementations and one preset:
  - `ollama` — default, native Ollama API (so context size can be set per
    request).
  - `openai-compat` — any OpenAI-style `/chat/completions` endpoint (hosted
    APIs, LM Studio, vLLM, etc.).
  - `gemini` — a *preset* of `openai-compat` pointing at Google's
    OpenAI-compatible endpoint, so users only supply a key and a model id.
  Rationale: a local-only tool is limited to people with a capable machine and
  several GB of disk to spare; one thin hosted-capable provider removes that
  adoption ceiling.
- Source code is only ever sent to the endpoint the user configured. Default
  is localhost. A non-local endpoint requires explicit acknowledgement (see
  *Privacy and remote endpoints*). No telemetry.
- Default local model: `qwen2.5-coder:14b`. Hosted providers have **no default
  model** (model ids go stale); the user must set `llm.model`.
- Caching is real from day one. The cache key for every LLM pass is a hash of
  *everything that feeds that pass* (see Layer 3).
- Grounding/confidence (spec's Layer 5) is **out of v1 scope** beyond the
  prompt rule and the reserved return-shape seam.

## Repo layout

```
codewhy/
├── CLAUDE.md                   # standing rules for the coding agent
├── pyproject.toml              # hatchling backend, single source of truth for deps
├── README.md
├── codewhy.toml.example        # identical to src/codewhy/templates/codewhy.toml
├── docs/                       # ARCHITECTURE.md, TESTS.md, PROJECT_SPEC.md
├── tasks/                      # ALL-TASKS.md + 01..09 work orders
├── logs/                       # one work log per task
├── scripts/                    # check_wheels.py, provider_smoke.py, dev_run_passes.py
├── eval/                       # quality evaluation (task 09)
├── .github/workflows/          # wheels-check.yml (task 02), ci.yml (task 09)
├── src/codewhy/
│   ├── __init__.py
│   ├── cli.py                  # typer app, all subcommands, progress output
│   ├── config.py               # loads/validates codewhy.toml, applies CLI overrides
│   ├── templates/              # codewhy.toml and toolignore templates written by `init`
│   ├── pipeline.py             # orchestrator: file -> module -> project passes (+ cache seam)
│   ├── ignore.py               # discovery: built-in list + .toolignore (pathspec), guards
│   ├── project.py              # ProjectModel: files, modules, other files
│   ├── entrypoint.py           # deterministic entry-point detection
│   ├── extraction/
│   │   ├── model.py            # FileStructure dataclass (language-agnostic)
│   │   ├── python_ast.py
│   │   └── ts_treesitter.py
│   ├── graph/
│   │   ├── python_resolver.py  # import -> project file (Python)
│   │   ├── js_resolver.py      # import -> project file (JS/TS)
│   │   └── builder.py          # networkx DiGraph + canonical edge lists
│   ├── cache/
│   │   ├── db.py               # sqlite3 connection + schema
│   │   └── keys.py             # input_key computation
│   ├── llm/
│   │   ├── provider.py         # abstract Provider interface
│   │   ├── ollama_provider.py
│   │   ├── openai_compat_provider.py
│   │   ├── budget.py           # token estimation + prompt budget
│   │   └── chunking.py         # AST-boundary chunking for oversized files
│   ├── summarize/
│   │   ├── schemas.py          # pydantic models, one per pass
│   │   ├── prompts.py          # templates + PROMPT_VERSION
│   │   ├── file_pass.py
│   │   ├── module_pass.py
│   │   └── project_pass.py
│   └── render/
│       ├── sections.py         # shared section builders (deterministic + LLM text)
│       ├── single_file.py      # PROJECT_DOCS.md, with budget levels
│       └── docs_tree.py        # codewhy-docs/ folder output
└── tests/
    ├── fixtures/
    └── ...
```

## Pipeline

```
User ── CLI (typer) ── config.py ── provider preflight + remote-ack check
                            │
                            ▼
                  Discovery (ignore.py → ProjectModel)
                            │
                            ▼
                    Extraction (Layer 1)
              ┌─────────────┴─────────────┐
        python_ast.py              ts_treesitter.py
                            │
                            ▼
              Import resolution + Graph (Layer 2)
                            │
                            ▼
        Summarization (Layer 4), every call gated by the cache (Layer 3):
              file_pass → module_pass → project_pass
        (each call: prompt assembly → budget check → provider)
                            │
                            ▼
                         Render
          PROJECT_DOCS.md  and/or  codewhy-docs/
```

`scan` order of operations:

1. Load config, apply CLI overrides, build the provider.
2. Remote-endpoint acknowledgement check, then provider preflight (fail fast,
   before any extraction work). Skipped for `--dry-run`.
3. Discover files, build `ProjectModel`, detect entry point.
4. Extract each file, resolve imports, build the graph.
5. Run `file_pass` for every file, then `module_pass` for every module, then
   `project_pass`; each is a cache lookup first.
6. Render output.
7. Delete cache rows for subjects that no longer exist; print the run summary.

## CLI surface

```
codewhy scan [PATH]                  # PATH defaults to "."
    --provider ollama|openai-compat|gemini   # overrides llm.provider
    --model <id>                     # overrides llm.model
    --output single-file|docs-tree|both   # default: single-file
    --full                           # ignore cache reads, regenerate and rewrite everything
    --dry-run                        # no LLM calls, no writes: print plan and estimates
    --yes                            # acknowledge sending code to a non-local endpoint
    --force                          # overwrite output files codewhy did not generate
    --quiet                          # no progress output; errors and summary line only
codewhy doctor [PATH]                # preflight only (accepts --provider / --model): provider
                                     # reachable, model present; prints the exact fix on failure
codewhy init [PATH]                  # writes codewhy.toml and .toolignore if absent
codewhy config show [PATH]           # resolved config (defaults + codewhy.toml + env)
codewhy inspect [PATH]               # developer command, hidden from --help: prints discovery,
                                     # extraction, graph, modules, entry point as JSON. No LLM.
```

`scan` is the only command that touches the pipeline. `init`, `doctor`,
`config show`, and `inspect` never write anywhere except `init` writing its two
files, and `init` never overwrites an existing file (it skips it and says so).

**Exit codes:** `0` all files documented (skips of unsupported/ignored files
don't count); `1` fatal error (bad config, provider unreachable, remote not
acknowledged, refused overwrite); `2` run completed but at least one file was
skipped due to a parse/size/decode problem or at least one pass ended in
`[generation failed]`.

**Progress output** (unless `--quiet`): one line per pass item, e.g.
`[7/18] file  src/solver.py  (generated)` / `(cached)`, then a final summary:
`18 files, 11 cached, 7 generated, 0 failed, 1 skipped. PROJECT_DOCS.md ≈ 9,400 tokens.`

**`--dry-run`** reports: files discovered and skipped (with reasons), cache
hits vs misses, number of LLM calls and estimated input tokens. File-pass
numbers are exact (prompts are assembled without calling the model).
Module/project-pass numbers are upper bounds because their prompts depend on
file summaries that may not exist yet.

## Config, ignore, and discovery

**`codewhy.toml`** (scanned-folder root, optional — defaults apply if absent):

```toml
[llm]
provider = "ollama"              # "ollama" | "openai-compat" | "gemini"
model = "qwen2.5-coder:14b"
host = "http://localhost:11434"  # ollama only
context_tokens = 16384           # see below
max_output_tokens = 1024         # provider default: 1024 (ollama, openai-compat), 8192 (gemini)
temperature = 0
seed = 0
timeout_seconds = 300
max_transient_retries = 3        # network errors, 429, 5xx (exponential backoff, honors Retry-After)

# --- gemini preset (Google's OpenAI-compatible endpoint; set a model id) ---
# provider = "gemini"
# model = "<model id>"              # required, no default shipped
# (fills in: base_url, api_key_env = "GEMINI_API_KEY", structured_output = "schema",
#  reasoning_effort = "low", send_seed = false, context_tokens = 32768, max_output_tokens = 8192)

# --- generic openai-compat example (no default model is shipped; set one) ---
# provider = "openai-compat"
# base_url = "https://<provider-host>/v1"
# api_key_env = "CODEWHY_API_KEY"   # name of the env var; the key itself is never stored in the file
# model = "<model id>"
# context_tokens = 32768
# structured_output = "prompt"      # "schema" | "json" | "prompt" (see Provider interface)
# send_seed = false                 # some endpoints reject unknown parameters
# reasoning_effort = "low"          # optional; omitted from requests when unset
# acknowledge_remote = false        # true = never prompt about sending code to the endpoint

[output]
mode = "single-file"             # "single-file" | "docs-tree" | "both"
dir = "codewhy-docs"             # docs-tree output directory (inside the scanned folder)
single_file_name = "PROJECT_DOCS.md"
token_budget = 20000             # single-file only; see Render levels

[extraction]
languages = ["python", "javascript", "typescript"]
max_file_bytes = 500000          # larger supported files are skipped with a warning

[project]
# Optional explicit entry point (project-relative path). Always overrides detection.
# entry_point = "src/main.py"
```

**`gemini` preset:** base URL `https://generativelanguage.googleapis.com/v1beta/openai/`,
`api_key_env` defaults to `GEMINI_API_KEY`, `structured_output = "schema"`,
`reasoning_effort = "low"`, `send_seed = false`, default `context_tokens`
32768, default `max_output_tokens` 8192. The user may override any of these;
`model` is always required. The larger output cap exists because Gemini counts
thinking tokens against it ("maximum number of tokens a response can generate,
including thought tokens", Gemini thinking docs, 2026-10-14) and
`reasoning_effort = "low"` alone can use a 1024-token thinking budget; with a
1024 cap the JSON could be cut off or empty. The live smoke check (task 04 log)
confirms or adjusts the value.

`max_output_tokens` when unset: 1024 for `ollama` and `openai-compat`, 8192 for
`gemini`. It must be smaller than the effective `context_tokens` (config error
otherwise).

`openai-compat` without the preset: `structured_output` unset means `prompt`
(no `response_format`), `send_seed` unset means `false`, and `api_key_env` unset
means no `Authorization` header (for local servers such as LM Studio). `base_url`
and `model` are required.

`context_tokens`: for `ollama` it is sent as `options.num_ctx` on **every**
request (never rely on Ollama's default); for `openai-compat` it is not sent
and is used only as the assumed context limit for budgeting, so it must not
exceed the real limit. Provider-specific defaults when unset: `ollama` 16384,
`openai-compat` 32768.

**`.toolignore`** (scanned-folder root, optional): `.gitignore` syntax, parsed
with `pathspec`. `.gitignore` itself is **never** read — it often excludes
things the user wrote and wants documented, and ties behavior to git.

**Built-in ignore list** (always applied, placed *before* `.toolignore`
patterns in one ordered pattern set, so later `!negation` lines in
`.toolignore` can re-include them, e.g. `!build/`): `node_modules/`, `.venv/`,
`venv/`, `__pycache__/`, `.git/`, `dist/`, `build/`, `.next/`, `*.min.js`,
`*.d.ts`, `.pytest_cache/`, `.mypy_cache/`, `.idea/`, `.vscode/`.

**Always excluded, not overridable** (codewhy's own artifacts, so a re-run
never documents its own output): `.codewhy/`, the configured output `dir`,
the configured `single_file_name`, `codewhy.toml`, `.toolignore`.

**Discovery rules:** ignored directories are pruned, not walked; symlinks are
never followed (no loops, no escaping the folder); a supported file larger
than `max_file_bytes` is skipped with a warning; a file that cannot be decoded
as UTF-8 (BOM tolerated) is skipped with a warning.

**Supported extensions:** Python `.py`; JavaScript `.js .jsx .mjs .cjs`;
TypeScript `.ts .tsx .mts .cts`.

## Module Definition

A **module** in v1 is any folder that directly contains at least one supported
source file, including the project root (module path `.`).

Module grouping is non-recursive: a module covers only the supported source
files directly inside that folder. Files in subfolders belong to those
subfolders' modules.

Every qualifying folder gets exactly one module document, including folders
with a single supported file. A folder containing only subfolders gets none.
Modules are ordered by path everywhere they are listed.

## Entry Point Detection

Deterministic, fixed priority; the first valid match wins. Metadata is read
from the scanned folder root only.

1. Explicit `project.entry_point` in `codewhy.toml` (must exist in the project,
   else it is a fatal config error).
2. Package metadata, accepted only when the target resolves to a file actually
   present in the project:
   - `pyproject.toml`: entries of `[project.scripts]`, then
     `[tool.poetry.scripts]`, in file order. A value `pkg.mod:func` is resolved
     by taking `pkg.mod` through the Python resolver (Layer 2). First
     resolvable entry wins.
   - `package.json`: `"main"` (resolved relative to `package.json`'s folder
     with the JS resolver rules); then `"scripts.start"`, handled **only** when
     it matches `node|ts-node|tsx|nodemon [flags] <path>` (flags = tokens
     starting with `-`); any other form (`react-scripts start`, `npm run x`)
     is not matched.
3. Conventional filenames, exact order: `main.py`, `__main__.py`, `app.py`,
   `index.js`, `index.ts`, `src/main.py`, `src/index.js`, `src/index.ts`.
4. Otherwise the entry point is `None`.

When `None`, the project-level prompt must explicitly instruct the model to
state that no conventional entry point was detected. The LLM must not infer or
guess one. `pyproject.toml` and `package.json` are read structurally for this
purpose only; they are not otherwise supported source formats.

## Unsupported Files

Only Python/JS/TS source files are parsed and documented in v1. All other
non-ignored files are excluded from extraction and summarization; their paths
appear in the project overview's deterministic **Other files in this project**
list (capped at 100 paths, then `… and N more`). No interpretation is
generated for them. README files get no special treatment in v1.

## Layer 1 — Extraction (deterministic, no LLM)

- `python_ast.py`: stdlib `ast`. Emits per file: functions (name, signature,
  docstring), classes (name, bases, methods, docstring), imports (module,
  names, level for relative imports), line count. Imports are collected from
  the **whole tree**, including inside functions, `try/except ImportError`,
  and `if TYPE_CHECKING` blocks.
- `ts_treesitter.py`: prebuilt grammar wheels (`tree-sitter-javascript`,
  `tree-sitter-typescript`; TS and TSX are separate grammars, `.jsx` uses the
  JS grammar). Same output shape. Collects static `import … from`,
  `export … from`, `require('literal')`, and dynamic `import('literal')`.
  Functions include declarations, `const f = () =>` / function expressions,
  class methods, and exported defaults.
- Shared `FileStructure` dataclass so Layers 2+ never branch on language.
  Fields: `path`, `language`, `source_text`, `line_count`, `functions`,
  `classes`, `types`, `imports`, `top_level_units`, `status` (`ok` |
  `skipped`), `warning`. `types` lists TypeScript interfaces, type aliases, and
  enums (name, kind, line range) so they appear in "Defined Here"; it is always
  empty for Python and JavaScript.
- `top_level_units`: ordered `(name, kind, start_line, end_line, children)`
  covering the file contiguously. Module-level statements and imports between
  definitions attach to the **following** unit (trailing ones to the last).
  Classes carry their methods as children. Chunking depends on this.
- Parse failure → `status="skipped"` with a warning; the extractor never
  raises on bad input. A skipped file is excluded from graph and docs and
  counted in the run summary.
- Never calls the LLM. Same input → byte-identical output.

## Layer 2 — Import resolution and dependency graph

`networkx.DiGraph`, one node per supported file, edge `A → B` when A imports
B. No edges between Python and JS/TS files. Cycles are allowed. Imports that do
not resolve to a project file are recorded as **node metadata**
(`external_imports`, top-level package name only) and never become edges. No
attempt is made to distinguish stdlib from third-party. Relative imports that do
not resolve (e.g. `./styles.css`, `./data.json`, `from . import missing`) are
recorded as `unresolved_imports` (raw specifier): never an edge, never an
external dependency, not rendered in docs, visible through `inspect`.

### Python resolution

For an import in file `F` (directory `D`, project root `R`):

- **Relative** (`from . import x`, `from ..a import b`, level `n`): base dir is
  `D` going up `n-1` levels. For `from .a import b` try, per imported name,
  the submodule `<base>/a/b.py` / `<base>/a/b/__init__.py` first (as for
  absolute imports below), else `<base>/a.py`, then `<base>/a/__init__.py`.
  For `from . import x` try, per imported name, `<base>/x.py`,
  `<base>/x/__init__.py`, else `<base>/__init__.py`. Targets are the sorted
  union over the imported names; `*` targets the module file itself.
- **Absolute** (`import a.b.c`, `from a.b import c`): try search roots in
  order — `D`, `R`, `R/src` (the first is what makes flat scripts like
  `main.py` doing `import utils` work). In each root, take the dotted name and
  find the **longest prefix** that maps to a project file (`a/b/c.py` or
  `a/b/c/__init__.py`, then `a/b.py` / `a/b/__init__.py`, then `a.py` /
  `a/__init__.py`); the first root that yields a match wins. For
  `from a.b import c`, a submodule `a/b/c.py` is preferred over the package
  file `a/b/__init__.py`.
- Self-imports produce no edge. Dynamic imports (`importlib`, `__import__`)
  are not resolved.

### JS/TS resolution

Only specifiers starting with `.` or `/` are candidates; everything else
(`react`, `lodash`, `@/utils`, `~/x`) is external. **v1 does not read
`tsconfig.json` `paths`/`baseUrl`, so alias imports appear as external** —
document this limitation in the README.

For a relative specifier from file `F`:
1. exact path if it already has a supported extension and exists;
2. specifier + each supported extension, in the order `.ts .tsx .js .jsx .mjs
   .cjs .mts .cts`;
3. specifier as a directory with `index` + each supported extension (same
   order);
4. TS ESM convention: if the specifier ends in `.js/.jsx/.mjs/.cjs` and that
   file does not exist, try the `.ts/.tsx/.mts/.cts` counterpart.

### Canonical edge lists

Fingerprinting and cache keys use a canonical representation: each edge as
`<source_relative_path> -> <destination_relative_path>`, `/` separators,
sorted lexicographically, joined with `\n`.

- A **module's relevant edges** are all edges where either endpoint belongs to
  that module.
- The **project edge list** is every resolved local edge.

### Derived data (deterministic)

- `Depends On` / `Used By` per file: sorted from the graph.
- **Most depended-on files**: top 5 files by in-degree (ties by path), zero
  in-degree excluded. Also used as the ordering for truncation in overflow
  ladders (see Layer 4).

## Layer 3 — Cache

Cache DB: `.codewhy/cache.db` inside the scanned folder.

```sql
CREATE TABLE meta (
  key   TEXT PRIMARY KEY,      -- 'schema_version'
  value TEXT NOT NULL
);

CREATE TABLE pass_cache (
  pass         TEXT NOT NULL,  -- 'file' | 'module' | 'project'
  subject      TEXT NOT NULL,  -- file path | module path | '' for project
  input_key    TEXT NOT NULL,  -- sha256 of everything that fed this pass (below)
  result_json  TEXT NOT NULL,  -- validated pass output
  generated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (pass, subject)
);
```

### The rule: key = hash of everything that feeds the pass

`input_key = sha256(canonical_json({pass, PIPELINE_VERSION, config_fp, inputs}))`

- `PIPELINE_VERSION`: a constant in code, bumped by hand whenever prompt
  templates, pass schemas, extraction output, or chunking behavior change.
- `config_fp`: hash of provider kind, model, `context_tokens`,
  `max_output_tokens`, `temperature`, `seed`, `reasoning_effort`,
  `structured_output`. (Changing the model or context
  size invalidates everything, as it should.)
- `inputs` per pass:
  - **file**: path, full `source_text`, sorted `imports` paths, sorted
    `imported_by` paths.
  - **module**: module path, for each member file `(path, Purpose text)`, the
    module's canonical relevant edges, sorted external dependency names.
  - **project**: entry point (or null), for each module
    `(path, Responsibility text)`, the canonical project edge list, the capped
    list of other-file paths used in the prompt.

Lookup: a row for `(pass, subject)` whose `input_key` equals the current key is
a hit; anything else is a miss (run the pass, upsert the row). `--full`
skips reads but still writes.

**The "smart cascade" is a property of this rule, not separate logic.**
Module inputs contain file *Purpose text* (not the whole file summary and not
the source), so:

- source edited but the file's Purpose text comes out identical → module key
  unchanged → module pass **not** re-run;
- file Purpose text changes → that module's key changes → module pass re-runs;
  project pass re-runs only if that module's `Responsibility` text changed;
- an import edge changes (`A→B` becomes `A→C`) → the relevant module keys and
  the project key change even if every summary text is identical;
- fields that don't enter a downstream prompt (e.g. a file's `Notes`) never
  cascade.

Caveats: with `temperature = 0` and a fixed seed local models are close to
deterministic, but hosted APIs may ignore `seed`, so "identical wording after a
source edit" is the best case, not the typical one. Do not promise users more
savings than that.

**Other rules**
- A `[generation failed]` result is **never written to the cache**, so the
  next run retries it.
- Every upsert is committed immediately. A run killed halfway resumes from
  where it stopped, which makes the cache double as a resume mechanism for
  long local-model runs.
- After a run, rows whose subject is no longer a current file/module are
  deleted (handles deleted and renamed files and vanished modules).
- Deterministic sections (`Depends On`, `Used By`, `Defined Here`, module and
  project structure lists) are rendered from current extraction/graph state on
  every run; the cache gates only LLM prose, so deterministic content can never
  be stale.
- Corrupt/unreadable DB or unknown `schema_version`: treat as empty, warn,
  rebuild; never crash.

## Layer 4 — Summarization (hierarchical, map-reduce)

### Documentation contract

Generated Markdown follows a fixed, renderer-controlled schema. **LLM output
is structured JSON per pass, never free-form Markdown.** The renderer combines
LLM prose with deterministic extraction/graph data. Structural facts are never
delegated to the LLM when they can be rendered from Layer 1 or Layer 2; the
LLM answers "why" and "how they fit together", not "what are the function
names".

Renderer rule: LLM strings are inserted as plain paragraphs; line-leading `#`
characters are escaped so model output can never break document structure.

**Pass schemas** (pydantic models in `summarize/schemas.py`):

| Pass | Fields |
|---|---|
| file | `purpose: str` (required, 1–3 sentences, explains why the file exists in this project), `notes: str \| null` |
| module | `responsibility: str`, `how_pieces_cooperate: str` |
| project | `overview: str`, `architecture: str` |

**File document:**
- `Purpose` — LLM, mandatory.
- `Depends On` — deterministic, mandatory.
- `Used By` — deterministic, mandatory.
- `Defined Here` — deterministic Layer 1 data (functions, classes, methods,
  signatures), mandatory.
- `Notes` — LLM, optional, omitted when null/empty.

**Module document (`_module.md` in tree mode):**
- `Responsibility` — LLM.
- `How the pieces cooperate` — LLM.
- `Files in this module` — deterministic.
- `Internal dependencies` — deterministic graph data.
- `External dependencies` — deterministic (`external_imports` union).

**Project overview (`PROJECT_OVERVIEW.md` in tree mode):**
- `Overview` — LLM.
- `Architecture` — LLM.
- `Entry Point` — deterministic detection result.
- `Modules` — deterministic module list.
- `Most depended-on files` — deterministic top 5 (omitted if none).
- `Other files in this project` — deterministic paths of unsupported files.

### Provider interface (`llm/provider.py`)

```python
class Provider(Protocol):
    is_remote: bool                           # drives the consent prompt
    endpoint_label: str                       # host shown in the consent message
    def preflight(self) -> None: ...          # raises ProviderError with the exact fix
    def generate_json(self, prompt: str, schema: dict, *,
                      max_output_tokens: int, temperature: float | None = None) -> dict: ...
```

- Nothing in `summarize/` imports a concrete provider; everything goes through
  the interface.
- Structured output uses the provider's native mechanism where it exists
  (Ollama `format` = JSON schema; OpenAI-style `response_format` json_schema
  where the endpoint supports it, else a JSON-only prompt instruction with
  tolerant extraction of the first JSON object). **Every response is
  validated against the pass's pydantic model regardless** — native structured
  output reduces failures, it does not replace validation.
- `ollama_provider.py`: native `/api/chat`; sends `options.num_ctx =
  context_tokens` on every call, plus `temperature` and `seed`.
- `openai_compat_provider.py`: `httpx`, bearer key read from the env var named
  by `api_key_env` at call time (never logged, never written to disk or cache).
  Sends `temperature` and `max_tokens` always; `seed` only if `send_seed`;
  `reasoning_effort` only if set (`max_tokens` is `max_output_tokens`).
  `structured_output`: `schema` sends
  `response_format` json_schema; `json` sends `response_format` json_object;
  `prompt` sends neither and relies on the prompt plus tolerant JSON extraction.
  Provider-facing JSON schemas are simplified (no `$defs`, no `anyOf`; optional
  fields typed as plain strings) because hosted endpoints accept only a subset.
- **Preflight (`ollama`):** `GET /api/version` must report Ollama **0.5.0** or
  newer (JSON-schema `format`); `GET /api/tags` must list the model (a bare name
  means `<name>:latest`). Connection failure → "Cannot reach Ollama at <host>.
  Start it with `ollama serve` …"; model missing → `ollama pull <model>`.
- **Preflight (`openai-compat`/`gemini`):** the key env var must be set; then
  `GET {base_url}/models`: 401/403 → fatal auth message; 404/405/501 → skip the
  check (endpoint has no listing); connection/timeout error → fatal
  unreachable message; a listing that lacks the model id → warning only (ids may
  be prefixed); 429/5xx on the listing → warning only. Gemini reports an invalid
  key as HTTP 400 "Please pass a valid API key" (observed 2026-10-20), so a 4xx
  whose body says the key is invalid is also an auth failure.
- **Error mapping:** 401/403 → `AuthError` (never retried); 429, 5xx, timeouts,
  connection errors → transient (backoff inside the provider); other 4xx →
  `BadRequestError` with a key-redacted body excerpt; unparseable JSON content
  → `MalformedOutput`. Schema validation and the retry-once-with-error policy
  live in the pass layer, not the provider. Transient backoff: 1s, 2s, 4s, …
  capped at 30s; a `Retry-After` header (capped at 60s) is used when it is
  longer.

### Prompt rules

Every pass prompt includes this instruction verbatim:

> Do not invent behavior, APIs, or functionality not evidenced in the provided
> structure or source. If intent cannot be confidently established, say so
> plainly rather than fabricating a plausible explanation.

This is a v1 reliability rule, not the deferred Layer 5 system. Prompts also
tell the model who the reader is (someone new to the codebase, possibly pasting
this into a chat later) and to write plain prose with no headings.

### Context budgeting (`llm/budget.py`)

- `estimate_tokens(text) = ceil(len(text) / 3)` — deliberately conservative for
  code. One function, so it can be swapped for a real tokenizer later.
- `prompt_budget = floor((context_tokens - max_output_tokens) * 0.9)`.
- **Invariant:** every prompt any pass sends has
  `estimate_tokens(prompt) <= prompt_budget`. Passes enforce this by the
  fallback ladders below, never by hoping.

### `file_pass`

Prompt contains: path, language, line count, the file's `imports` and
`imported_by` project paths (this is what lets the model explain *why the file
exists* — paths only, never neighbor summaries, so neighbor changes don't
cascade), Layer 1 structure, and the **full source whenever the assembled
prompt fits the budget**. There is no line-count threshold; the budget is the
only gate.

If the full prompt does not fit → **chunking**.

Shortcut: a file with no non-blank, non-comment lines (e.g. an empty
`__init__.py`) gets the fixed purpose `"Empty file."` with no LLM call.

### Chunking (locked)

When a file's prompt exceeds the budget, split along `top_level_units`
(never naive token chunking, never mid-function):

1. Greedily pack consecutive units into chunks whose chunk prompt fits the
   budget. Each chunk prompt also carries the file's full list of top-level
   names (cheap, gives global context).
2. A **class** larger than a chunk is split at method boundaries (class header
   + each method become units).
3. A single **function/method** larger than a chunk is truncated: head kept
   (signature + docstring + as much body as fits), followed by
   `[...truncated, N lines omitted]`. The file is never failed over this.
4. Each chunk is summarized independently, then exactly **one** combine call
   merges chunk summaries into the file-level schema. If the combine prompt
   would exceed the budget, each chunk summary is cut to its first sentence
   first.
5. Chunk calls are not individually cached; the cache stores the final file
   result under the file's `input_key`.

### `module_pass` and `project_pass` overflow ladders

Inputs are bounded by construction (Purpose / Responsibility text, not full
summaries), but large projects can still overflow. Fixed ladder, deterministic,
stop at the first step that fits:

1. Full text (all Purposes / all Responsibilities).
2. First sentence of each.
3. Include as many items as fit ordered by in-degree (desc, ties by path) and
   append `N more files/modules omitted from this prompt`.

The same text is what enters the cache key, so the ladder is part of
determinism.

`module_pass`: member Purposes + relevant edges + external dependency names →
`responsibility`, `how_pieces_cooperate`.
`project_pass`: module Responsibilities + deterministic entry point + project
edge list (module-to-module summary of it) + capped other-file paths →
`overview`, `architecture`. It must not infer an entry point when detection
returned `None`.

### Retries and failure

- **Malformed output** = unparseable JSON, schema-validation failure, or an
  empty required string. Retry **once** with the validation error appended to
  the prompt (a plain identical retry at `temperature = 0` would just fail the
  same way; the retry may also use a small non-zero temperature). On second
  failure the result is `[generation failed]` for that file/module/project and
  the run continues (exit code 2).
- **Transient provider errors** (network, 429, 5xx): exponential backoff up to
  `max_transient_retries`, honoring `Retry-After`. Exhausted → same
  `[generation failed]` handling.

## Layer 5 — Grounding (reserved, not implemented in v1)

Interface seam only: every pass function returns
`PassResult(data: <pass pydantic model> | None, metadata: dict,
generated_at: datetime, failure: str | None)` with `metadata` always `{}` in v1;
`data` is `None` exactly when `failure` is set. A future confidence/grounding pass slots into this shape
without touching call sites. README states: "not implemented — reserved for
v2." First planned v2 item: a deterministic check that identifiers and paths
mentioned in LLM prose actually exist in extraction output.

## Output

### Default: single file

`<root>/PROJECT_DOCS.md`, ordered: header block → short deterministic preamble
→ project overview → module sections (path order) → file sections (path order).

The **preamble** is a fixed paragraph so a chat model that receives the file
cold knows what it is. Exact text (frozen by a golden-file test):

> This document describes a codebase. It was generated automatically by
> codewhy from a local folder: structural facts (definitions, dependencies,
> entry point) come directly from parsing the code, while the explanations were
> written by a language model and may contain mistakes. All paths are relative
> to the project root. Sections: Overview, Modules, Files.

The exact heading layout is specified in task 06 and then frozen by golden-file
tests.

Header block (single, no per-section frontmatter):
```markdown
---
generated_by: codewhy v0.1.0
source: project
generated_at: <ISO 8601>
---
```

### Render levels (single-file only)

The renderer picks the **lowest-numbered level** whose estimated size
(`estimate_tokens` on the full output) is ≤ `output.token_budget`:

| Level | Content |
|---|---|
| 0 full | everything in the documentation contract |
| 1 compact | drops `Notes` and `Defined Here` on every file |
| 2 minimal | file sections become one `path — first sentence of Purpose` line; module sections keep `Responsibility` only; project overview keeps `Overview`, `Architecture`, `Entry Point` |

If Level 2 still exceeds the budget, emit Level 2 and warn. The CLI prints the
chosen level and the estimated token count. Level selection is pure
rendering — no extra LLM calls. docs-tree mode is always Level 0.

### docs-tree mode

`<root>/codewhy-docs/` mirroring project structure (`dir` is configurable; the
default is deliberately **not** `docs/` to avoid colliding with a user's own
folder):
- one `<original-filename>.md` per supported source file;
- one `_module.md` per qualifying module, in that folder's mirrored location
  (the root module's is `codewhy-docs/_module.md`);
- one `codewhy-docs/PROJECT_OVERVIEW.md`.

Each generated file carries frontmatter:
```markdown
---
generated_by: codewhy v0.1.0
source: <relative path to the original file, or "project">
generated_at: <ISO 8601>
---
```

`both` writes the single file and the tree.

### Timestamps and idempotence

`generated_at` is the `generated_at` carried by the pass result behind that
document (file/module/project pass; the single-file header uses the latest of
all); the cache persists and restores it. It is **not** the wall-clock time of
the run, so a re-run with no changes produces
**byte-identical** output files — no diff noise for users who commit docs.

### Overwrite protection

If the target file already exists and does not begin with codewhy frontmatter
(`generated_by: codewhy`), the run exits `1` with a message unless `--force`
is given. In docs-tree mode, stale per-file docs for files that no longer exist
are removed, but only files carrying codewhy frontmatter.

## Privacy and remote endpoints

- `ollama` with a loopback host (`localhost`, `127.0.0.1`, `::1`) is local.
  Everything else (including `openai-compat` pointed anywhere) is **remote**.
- Before the first LLM call to a remote endpoint, print the endpoint host and
  the sentence "Your source code will be sent to this endpoint", then require
  confirmation: interactive `y/N` prompt, or `--yes`, or
  `llm.acknowledge_remote = true`.
- No TTY and no acknowledgement → exit `1` with the message; never send
  silently.
- The API key is read only from the environment variable named in config.
- Because hosted free tiers can have different data-use terms from paid tiers,
  the README must tell users to check their provider's terms before pointing
  `codewhy` at private code.
- Deferred: secret redaction (API keys hard-coded in source would be sent and
  could be echoed into docs).

## Failure boundaries

| Failure | Handling |
|---|---|
| Provider unreachable (Ollama down / bad host / remote 401, DNS) | Fail fast before extraction, exit 1, message with the fix (`ollama serve`, or check `base_url`/`api_key_env`) |
| Ollama model not pulled | Fail fast, exit 1, message with `ollama pull <model>` |
| Remote endpoint not acknowledged | Exit 1 before any call |
| tree-sitter grammar missing for a found file | Skip file, warn, continue (exit 2) |
| Malformed Python / JS / TS (parse error) | Skip file, warn, continue (exit 2) |
| File over `max_file_bytes` or undecodable | Skip file, warn, continue (exit 2) |
| SQLite cache corrupt / unknown schema | Treat as empty, warn, rebuild, continue |
| LLM output malformed | Retry once with error appended; then `[generation failed]`, not cached, continue (exit 2) |
| Transient provider error | Backoff retries; then `[generation failed]`, continue (exit 2) |
| Output target exists and isn't codewhy's | Exit 1 unless `--force` |
| Single function larger than chunk budget | Truncate with marker (not a failure) |

## Build tasks

Implementation is split into nine work orders in `tasks/`, executed strictly in
order; each ends demoable with its tests green (see `TESTS.md`). Each task is
the detailed instruction set; this table is only the map.

| Task | Delivers | Layers |
|---|---|---|
| 01 | Scaffold, config, discovery/ignore, module model, `init`, `config show` | — |
| 02 | Python + JS/TS extraction, `FileStructure`, tree-sitter wheel verification | 1 |
| 03 | Import resolution, graph, entry point, `inspect` | 2 |
| 04 | Providers (ollama, openai-compat, gemini preset), budget, schemas, consent, `doctor` | 4 (foundation) |
| 05 | Prompts, file/module/project passes, chunking, ladders, retries, orchestrator | 4 |
| 06 | Rendering (single-file, docs-tree, levels), real `scan` wiring | Output |
| 07 | Cache, incremental runs, resume, idempotence | 3 |
| 08 | `--dry-run`, progress, exit codes, failure-boundary matrix, atomic writes | CLI |
| 09 | Packaging, CI, README, quality evaluation, release | Ship |

The tree-sitter wheel check (macOS arm64, Linux x86_64, Windows × Python
3.11–3.13) happens in task 02 so the "one command, no compiler" claim is
tested in week one, not at release.

## Locked implementation decisions

- Name: `codewhy` (package, CLI, `codewhy.toml`, `.codewhy/`).
- Python `>=3.11` (uses stdlib `tomllib`).
- Dependencies: `typer`, `rich`, `networkx`, `tree-sitter`,
  `tree-sitter-javascript`, `tree-sitter-typescript`, `pathspec`, `pydantic>=2`,
  `httpx`. Dev: `pytest`, `pytest-mock`. Nothing else without updating this
  list.
- Providers: `ollama` (default), `openai-compat`, and the `gemini` preset of
  `openai-compat`. No hosted default model.
- `PassResult` carries `generated_at`; the cache persists it.
- `inspect` is a hidden developer command; it is the demo surface for tasks 01–03.
- Default local model `qwen2.5-coder:14b`; default `context_tokens` 16384.
- `temperature = 0`, `seed = 0` by default.
- Output default: single file `PROJECT_DOCS.md` with render levels and a
  20000-token budget. No file-count threshold, no "auto" mode.
- docs-tree directory default `codewhy-docs/`.
- Cache DB at `.codewhy/cache.db`; key-based (`input_key`), not state-diffing.
- No source-line threshold for including source in `file_pass`; the token
  budget is the only gate. Token estimate is `len/3`.
- `.gitignore` is never read. `.toolignore` may negate built-in ignores.
- `src/` package layout.
- Module definition: folder-based, non-recursive, root counts.
- Entry-point priority as specified above.
- Unsupported files appear only in the deterministic "Other files" list.
- LLM passes return JSON validated by pydantic; the renderer owns Markdown.
- Graph changes participate in cache keys via canonical edge lists.
- Deterministic sections are re-rendered on every run.
- Frontmatter timestamps come from cache rows, so unchanged re-runs are
  byte-identical.

## Explicitly deferred (not v1)

- Layer 5 grounding/confidence (seam reserved; identifier-existence check is
  the first planned item)
- Secret redaction before sending code to remote endpoints
- `tsconfig.json` `paths` / `baseUrl` alias resolution
- Graph-clustering / PageRank-style module grouping and importance ranking
- Additional provider kinds beyond the two above
- README-aware project understanding
- Non-source project metadata beyond entry-point detection
- Real tokenizer-based token counting
- More than two languages
