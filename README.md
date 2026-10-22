# codewhy

**Point codewhy at a folder of Python / JavaScript / TypeScript code and get
one Markdown file that explains what each file does, why it exists, and how
the pieces connect. Paste it into any chat model and ask about the codebase.**

```console
$ uvx codewhy scan .
[1/12] file  constants.py  (generated)
…
9 files, 0 cached, 12 generated, 0 failed, 0 skipped. PROJECT_DOCS.md ≈ 3,476 tokens (level 0).
```

An excerpt of real output (from [`examples/codewhy/PROJECT_DOCS.md`](examples/codewhy/PROJECT_DOCS.md),
codewhy run on its own source):

<!-- SAMPLE-EXCERPT: filled from examples/codewhy/PROJECT_DOCS.md -->

Structural facts (definitions, imports, `Depends On` / `Used By`, entry point)
come from parsing the code. The explanations are written by a language model
and can be wrong.

---

## Quickstart

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/) (or pipx). Pick
one of three ways to run the model. Run the commands in the project folder you
want to document.

> **Before the first PyPI release**, replace `uvx codewhy` with
> `uvx --from git+https://github.com/Aditya-Sinha-7981/CodeWhy codewhy`.

### A. Gemini (hosted, fastest to try)

1. Create an API key at <https://aistudio.google.com/apikey>.
2. Put it in your environment. codewhy reads it from `GEMINI_API_KEY` and never
   stores it:

   ```bash
   export GEMINI_API_KEY="your-key"           # macOS / Linux
   # PowerShell: $env:GEMINI_API_KEY = "your-key"
   ```

3. Scan:

   ```bash
   uvx codewhy scan . --provider gemini --model gemini-3.6-flash
   ```

   codewhy shows the endpoint and asks before sending your code; add `--yes`
   to skip the question in scripts. Model ids change over time: the one above
   was current in October 2026. Find the current ones at
   <https://ai.google.dev/gemini-api/docs/models>. Check Google's data-use terms
   first; free-tier terms can differ from paid ones.

### B. Ollama (fully local, nothing leaves your machine)

1. Install Ollama from <https://ollama.com> and start it (`ollama serve`, or
   the desktop app).
2. Pull the default model, about 9 GB:

   ```bash
   ollama pull qwen2.5-coder:14b
   ```

3. Scan:

   ```bash
   uvx codewhy scan .
   ```

On a machine with 16 GB of RAM or less, the 14B model can be very slow. The
7B model (`ollama pull qwen2.5-coder:7b`, then `uvx codewhy scan . --model
qwen2.5-coder:7b`) documented a 9-file project in under a minute on an Apple
Silicon laptop.

### C. Any other OpenAI-compatible endpoint

Run `uvx codewhy init` once, then edit the `[llm]` section of the
`codewhy.toml` it writes:

```toml
[llm]
provider = "openai-compat"
base_url = "https://<provider-host>/v1"
api_key_env = "CODEWHY_API_KEY"    # the NAME of the environment variable holding the key
model = "<model id>"
structured_output = "prompt"       # "schema" if the endpoint supports JSON-schema output
```

```bash
export CODEWHY_API_KEY="your-key"
uvx codewhy doctor .               # checks the endpoint, key and model; sends no code
uvx codewhy scan .
```

### Then

Open `PROJECT_DOCS.md`, paste it into your chat model, and ask your question:
"Where is X handled?", "What breaks if I change Y?", "Which file do I edit to
add Z?". Re-run `codewhy scan .` after you change code. Unchanged files are
served from the cache, so the re-run costs nothing.

Want the cost first? `uvx codewhy scan . --dry-run` prints the files, the
cache hits and an estimate of model calls and input tokens. It makes no calls
and writes nothing.

---

## What you get

`PROJECT_DOCS.md` in the scanned folder, in this order:

1. A short header and a fixed preamble that tells a chat model what the
   document is.
2. **Overview**:
   - what the project does and how it fits together;
   - the detected entry point;
   - one line per module (folder);
   - the most depended-on files;
   - the other (non-code) files.
3. **Modules**, one per folder: responsibility, how its files cooperate, its
   files, and its internal and external dependencies.
4. **Files**, one per source file:
   - Purpose;
   - `Depends On` / `Used By` (from the import graph);
   - `Defined Here` (classes, methods, functions, TS types, with signatures
     verbatim);
   - Notes, when the model has one.

The file is sized for pasting. codewhy estimates its size (characters ÷ 3,
not a real tokenizer) and picks the most detailed **render level** that fits
`output.token_budget` (default 20,000):

| Level | Content |
|---|---|
| 0 | everything above |
| 1 | drops `Notes` and `Defined Here` |
| 2 | one line per file and module, plus the overview |

If even level 2 is over budget it is still written, with a warning. The
summary line always tells you the level and the estimate.

`--output docs-tree` instead writes `codewhy-docs/`: one Markdown file per
source file plus `_module.md` per folder and `PROJECT_OVERVIEW.md`, always at
full detail. `--output both` writes both.

codewhy never overwrites a file it did not write (one without its
`generated_by: codewhy` header) unless you pass `--force`. In `codewhy-docs/`
it only deletes stale documents it generated itself.

## Configuration

Everything works without a config file. `codewhy init` writes a commented
`codewhy.toml` and `.toolignore`, and never overwrites existing ones.
`codewhy config show` prints the resolved settings. Command-line flags
(`--provider`, `--model`, `--output`) override the file.

| Key | Default | Meaning |
|---|---|---|
| `llm.provider` | `"ollama"` | `"ollama"`, `"gemini"` or `"openai-compat"` |
| `llm.model` | `qwen2.5-coder:14b` for Ollama; none for hosted | model id (required for hosted providers) |
| `llm.host` | `http://localhost:11434` | Ollama only |
| `llm.base_url`, `llm.api_key_env` | set by the Gemini preset | OpenAI-compatible endpoint and the *name* of the key's environment variable |
| `llm.context_tokens` | 16384 (Ollama), 32768 (hosted) | context window codewhy plans prompts for |
| `llm.max_output_tokens` | 1024; 8192 for Gemini (thinking counts) | reply cap |
| `llm.temperature`, `llm.seed` | `0`, `0` | keep output as repeatable as the provider allows |
| `llm.timeout_seconds`, `llm.max_transient_retries` | `300`, `3` | per request; retries with backoff on network errors, 429 and 5xx |
| `llm.structured_output` | per provider | `"schema"`, `"json"` or `"prompt"` |
| `llm.acknowledge_remote` | `false` | `true` = don't ask before sending code to a remote endpoint |
| `output.mode` | `"single-file"` | `"single-file"`, `"docs-tree"` or `"both"` |
| `output.single_file_name` / `output.dir` | `PROJECT_DOCS.md` / `codewhy-docs` | output names inside the scanned folder |
| `output.token_budget` | `20000` | target size of `PROJECT_DOCS.md` |
| `extraction.languages` | python, javascript, typescript | languages to document |
| `extraction.max_file_bytes` | `500000` | larger files are skipped |
| `project.entry_point` | detected | force the entry point (project-relative path) |

### `.toolignore`

codewhy documents every supported file it finds, except built-in ignores
(`node_modules/`, `.venv/`, `venv/`, `__pycache__/`, `.git/`, `dist/`,
`build/`, `.next/`, `*.min.js`, `*.d.ts`, tool caches) and its own outputs.
**It does not read `.gitignore`**: a file you keep out of git is still
documented unless you list it in `.toolignore`. The syntax is the same as
`.gitignore`, including `!` to re-include something a built-in rule excludes:

```gitignore
tests/fixtures/
*.generated.ts
!build/
```

## How it works

```text
discover files (.toolignore) ─► parse (Python ast, tree-sitter JS/TS) ─► import graph + entry point
        │                                                                    │
        └──────────── deterministic facts ───────────────────────────────────┘
                                   │
     file pass (why each file exists) ─► module pass (each folder) ─► project pass (overview)
                 every model call is checked against the cache first
                                   │
                         render PROJECT_DOCS.md
```

- **Facts vs. prose.** Definitions, signatures, imports, `Depends On` /
  `Used By`, module membership and the entry point come from parsing and are
  re-derived on every run. Only the explanations come from the model.
- **Big files** are split along top-level definitions, summarized in parts,
  then combined. A single function too large for any prompt is truncated
  with a visible marker. That is not a failure.
- **The cache** (`.codewhy/cache.db`, which ignores itself in git) keys every
  model answer by a hash of exactly what went into its prompt, plus the model
  settings.
  - An unchanged re-run makes zero calls and writes byte-identical files, so
    committed docs don't churn.
  - An interrupted run resumes where it stopped.
  - Editing a file re-runs that file; its module re-runs only if the file's
    explanation came out different, and the overview only if the module's
    did.
  - **Honest caveat:** models usually reword an explanation after any edit,
    even at temperature 0. In our runs the explanation changed after every
    edit, so an edit typically re-ran the file, its module and the overview:
    3 of 12 calls on a 9-file project. "Only the edited file" is the best
    case, not the usual one.
  - `--full` regenerates everything.

## Privacy and data

- **The default is fully local.** With Ollama on `localhost`, your code never
  leaves your machine.
- **Remote endpoints need your OK.** Gemini, any OpenAI-compatible URL, or
  Ollama on another host: before the first call codewhy names the endpoint,
  says your source code will be sent there, and waits for `y`. Without a
  terminal it stops unless you passed `--yes` or set
  `llm.acknowledge_remote = true`.
- **What is sent:** the contents of every documented source file, plus
  file paths and summaries, go to the configured endpoint. That includes any
  secrets hard-coded in that source; codewhy does not redact them.
- **Check your provider's terms.** Hosted providers' data-use terms differ,
  and free tiers can allow training on your inputs.
- **API keys** are read only from the environment variable named in the
  config, at call time. They are never written to `codewhy.toml`, the cache,
  logs or error messages.
- **No telemetry.** codewhy contacts nothing except the model endpoint you
  configured.
- **The cache** holds only the validated model answers and timestamps, never
  your source code, prompts or keys.

## Limitations

- **Languages:** Python, JavaScript and TypeScript only (`.py .js .jsx .mjs
  .cjs .ts .tsx .mts .cts`). Other files are listed by name only.
- **Path aliases** such as `@/components/x` (from `tsconfig.json` `paths` or
  bundler config) are not resolved. In v1 they show up as *external*
  dependencies, so `Depends On` / `Used By` miss those links.
- **Python syntax newer than the Python running codewhy** (for example 3.13
  syntax under 3.11) fails to parse, and that file is skipped with a
  `skipped <file> (SyntaxError …)` line and exit code 2. Run codewhy with the
  same or a newer Python than your project uses.
- **Explanations can be wrong.** They are model-generated. codewhy does not
  check them against the code: "grounding" (Layer 5) is reserved for a later
  version and **not implemented**. Treat the prose as a strong hint and the
  structural sections as facts.
- **Token counts are estimates** (characters ÷ 3), printed with `≈`.
- **Cache savings** are best-case when a model rewords its answers (see *How
  it works*).
- **`--dry-run` numbers:** exact for the file pass; upper bounds for the
  module and overview passes, which depend on summaries that don't exist yet.
- **Writes are atomic per file**, not per folder. A `codewhy-docs/` run
  killed mid-write can leave a mix of old and new documents until the next
  run.
- **Windows:** the CI matrix includes Windows (see Development). Terminal
  output falls back to ASCII when the console cannot show `—`, `→` or `≈`;
  files are always UTF-8.

## FAQ

**Why didn't it find my `@/…` imports?** Path aliases aren't resolved in v1
(see Limitations). They appear under external dependencies.

**Why was my file skipped?** The `skipped <path> (<reason>)` lines say why:
- `too_large`: over `extraction.max_file_bytes`;
- `undecodable`: not UTF-8;
- `unreadable`: permissions, or the file vanished mid-run;
- a syntax error with its line number: it didn't parse, possibly newer syntax
  than your Python.

Files you excluded, and unsupported file types, are not "skipped". They are
simply not documented.

**How do I re-run cheaply?** Just run `codewhy scan .` again. Only what
changed is regenerated. `--dry-run` shows what that would be.

**Is the explanation always right?** No. The structural sections are
reliable; the prose is a language model's reading of the code. Spot-check it.

**It's slow with Ollama.** Use a smaller model (`--model
qwen2.5-coder:7b`), or a hosted provider. A Ctrl-C keeps everything finished
so far; the next run resumes.

**Where do I see what happened in detail?** Unexpected errors print a
one-line `internal error (…)`. Re-run with `CODEWHY_DEBUG=1` for the
traceback.

## Commands, exit codes and `--dry-run`

```text
codewhy scan [PATH] [--provider P] [--model M] [--output single-file|docs-tree|both]
                    [--full] [--dry-run] [--yes] [--force] [--quiet]
codewhy doctor [PATH] [--provider P] [--model M]   # check provider, key and model; sends no code
codewhy init [PATH]                                # write codewhy.toml and .toolignore
codewhy config show [PATH]                         # print the resolved configuration
```

| Exit code | Meaning |
|---|---|
| `0` | every supported file documented |
| `1` | fatal, nothing new written. Covers bad config, provider unreachable, model missing, remote endpoint not acknowledged, refused overwrite, rejected key, no supported files, unwritable output, cache locked. |
| `2` | finished, but at least one file was skipped or one explanation is `[generation failed]` (it is retried on the next run) |
| `130` | interrupted (Ctrl-C). Finished work is saved in the cache; re-run to resume. |

`--dry-run` builds no provider, asks nothing, and writes nothing (not even the
cache). It prints:
- discovered and skipped files;
- cache hits;
- the number of model calls and estimated input tokens per pass;
- for remote providers: "This would send ≈ N tokens of your source to
  `<host>`".

Progress is one line per item. On a terminal it adds a spinner; piped or in
CI it is plain text. `--quiet` keeps only errors and the summary.

## Development

```bash
git clone https://github.com/Aditya-Sinha-7981/CodeWhy && cd CodeWhy
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"        # Windows: .venv\Scripts\python
.venv/bin/python -m pytest -q                      # offline suite; no model needed
```

These are the same checks CI runs (`.github/workflows/ci.yml`: macOS, Linux
and Windows × Python 3.11–3.13, then an install-from-wheel job):

```bash
.venv/bin/python scripts/check_wheels.py           # tree-sitter grammar wheels load offline
uv build                                           # or: python -m pip install build && python -m build
CODEWHY_WHEEL=dist/codewhy-0.1.0-py3-none-any.whl .venv/bin/python -m pytest -q tests/test_packaging.py
uv venv /tmp/cw-clean && uv pip install --python /tmp/cw-clean/bin/python dist/codewhy-0.1.0-py3-none-any.whl
/tmp/cw-clean/bin/python scripts/wheel_smoke.py --codewhy /tmp/cw-clean/bin/codewhy --version 0.1.0
```

`scripts/wheel_smoke.py` starts `tests/support/stub_ollama.py`, a fake Ollama
server. It then scans a copy of a fixture with the installed wheel and checks
that a second scan makes zero model calls.

Live tests talk to a real model and are opt-in:

```bash
.venv/bin/python -m pytest -q -m live_llm                          # default model via Ollama
CODEWHY_LIVE_PROVIDER=gemini CODEWHY_LIVE_MODEL=gemini-3.6-flash .venv/bin/python -m pytest -q -m live_llm
```

Design and contracts live in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
the test plan in [`docs/TESTS.md`](docs/TESTS.md), and the work orders in
[`tasks/`](tasks/). The quality evaluation is in [`eval/`](eval/README.md).

### Releasing (maintainers)

Releases use PyPI **trusted publishing**; no API token is stored.

One-time setup:
1. On PyPI and on TestPyPI, add a pending trusted publisher for project
   `codewhy`. Use owner `Aditya-Sinha-7981`, repository `CodeWhy`, workflow
   `release.yml`, and environment `pypi` (on TestPyPI: `testpypi`).
2. Create the GitHub environments `pypi` and `testpypi`. Requiring a reviewer
   on `pypi` is recommended.

Each release:
1. Set the version in `src/codewhy/__init__.py` and add a `CHANGELOG.md`
   entry.
2. Run *Actions → Release → Run workflow*. This uploads to TestPyPI; check it
   with
   `uvx --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ codewhy --version`.
3. `git tag v0.1.0 && git push origin v0.1.0` publishes to PyPI. The workflow
   refuses a tag that doesn't match the package version or has no changelog
   entry.

## License

[MIT](LICENSE)
