# codewhy Task Specification

> **This file is a work order for the assigned developer and their AI coding agent.**
>
> This task defines the scope of the assigned work. Do not expand the scope without explicit approval from the project lead.

---

## Task Information

**Task:** `Ship it: packaging, CI, README, quality evaluation, release`

**Task ID:** `CW-09` (index: `tasks/ALL-TASKS.md`)

**Depends on:** CW-01 through CW-08

**Branch:** `task/09-ship` (suggested; create only when the user wants a branch)

**Priority:** `High`

**Status:** `Not Started`

**Work log:** `logs/09-ship.md`

---

# 1. Goal

## Objective

Make the claim "one command, anyone can run it" true on other people's machines, and prove the output is actually useful. Deliverables:

1. **Release-quality packaging:** metadata, license, version, wheel contents verified, `uvx`/`pipx` smoke tests.
2. **CI:** unit/integration matrix on macOS, Linux, Windows × Python 3.11–3.13; a wheel job that installs the built wheel in a clean environment and runs a scan against a stub Ollama-API server.
3. **README** that gets a stranger from zero to a pasted result in under five minutes, and states limitations honestly.
4. **Quality evaluation** (`eval/`): questions with ground truth, a runner, a mention-check script, and a written results table.
5. **Demo artifacts** (`examples/`): generated docs for real projects.
6. **Release process:** PyPI name, TestPyPI dry run, tagged release via trusted publishing (or documented manual `twine`).

## Why This Exists

Most "I built a CLI" projects die at the README. And without the evaluation you have a tool you believe in, not one you can show works. This task produces the evidence: other people can install it, and answers about a codebase are measurably better with the generated file than without.

---

# 2. Authoritative Documentation

* `CLAUDE.md`
* `docs/ARCHITECTURE.md` — *Product goals*, *Privacy and remote endpoints*, *CLI surface* (exit codes), *Explicitly deferred*, *Layer 3* caveats
* `docs/TESTS.md` — *Quality evaluation*, *CI (Phase 5)*, *E2E*
* Task logs `logs/01…08` — limitations discovered during the build (alias imports, parser-version dependence, observed cascade savings, Gemini findings, Windows findings) must flow into the README.

---

# 3. Scope

## In Scope

* `pyproject.toml` metadata, `LICENSE`, `CHANGELOG.md`, `README.md`.
* `.github/workflows/ci.yml` (extends/replaces `wheels-check.yml`), release workflow (or documented manual release).
* `tests/support/stub_ollama.py`: a tiny stdlib HTTP server implementing the Ollama endpoints codewhy uses (`/api/version`, `/api/tags`, `/api/chat` returning schema-valid JSON).
* `eval/` (runner, mention checker, corpus definition, question files, `RESULTS.md`).
* `examples/` (generated docs for 2 projects).
* Full E2E suite runs (`live_llm`) and results recorded.

## Out of Scope

* New features, new providers, new flags, grounding/Layer 5.
* A website, docs site, logo, social-media or announcement text.
* Telemetry/analytics of any kind.
* Publishing under a different name; changing the architecture's contracts (if the evaluation reveals a prompt weakness, record it as a v2 item unless it is a bug in task 05's prompts, which may be fixed with a `PROMPT_VERSION` bump).

Do not implement "while we're here" improvements outside this scope.

---

# 4. Allowed Files / Areas

```text
pyproject.toml                                (metadata only)
LICENSE
CHANGELOG.md
README.md
.github/workflows/ci.yml
.github/workflows/release.yml                 (optional; see Requirement 5)
tests/support/stub_ollama.py
tests/test_stub_ollama.py
tests/test_e2e_live.py                        (extend)
eval/README.md
eval/corpus.toml
eval/questions/<project>.toml
eval/run_eval.py
eval/check_mentions.py
eval/RESULTS.md
examples/<project-a>/PROJECT_DOCS.md
examples/<project-b>/PROJECT_DOCS.md
logs/09-ship.md
```

### May Modify If Required

```text
src/codewhy/summarize/prompts.py   (prompt fixes justified by the evaluation; bump PROMPT_VERSION; log it)
docs/ARCHITECTURE.md               (README-relevant facts that proved wrong — logged)
```

---

# 5. Dependencies

| Dependency | Type | Notes |
|---|---|---|
| CW-01–CW-08 | Hard | |
| GitHub repository with Actions enabled | Environment | Required for the CI matrix and wheel job. If absent: **Not run (no remote)**; do not claim CI passes. |
| PyPI and TestPyPI accounts; the name `codewhy` (free as of the architecture date — re-verify) | Environment | Publishing is the **user's** action; this task prepares and documents it. Do not publish without explicit authorization. |
| Real LLM access (Ollama and/or Gemini) | Environment | For the evaluation and the live E2E. |
| 3–4 projects for the evaluation corpus | Environment | At least two of the user's own messy projects, plus one or two small open-source projects the user did not write. |

### Dependency Status

* [x] Dependency exists and is ready
* [ ] Dependency is being developed separately
* [x] Dependency requires coordination (GitHub, PyPI, LLM access, corpus)
* [ ] No external dependency

---

# 6. Contracts That Must Be Preserved

### Architecture

* The README must state, accurately: supported languages (Python, JS/TS only); output behavior (single file by default, token budget, render levels); the three provider routes; exit codes; `--dry-run`; that **alias imports (`@/…`) appear as external** in v1; that Python files using syntax newer than the running interpreter are skipped; that prose may contain mistakes (model-generated) while structural facts come from parsing; that cache savings are best-case rather than typical when the model's wording changes; that Layer 5 grounding is **not implemented** (reserved for v2); what `.toolignore` does and that `.gitignore` is not read.
* Privacy statements in the README: default is fully local; remote endpoints require acknowledgement; source code is sent to the configured endpoint; users must check their provider's data-use terms (free tiers differ); no telemetry; API keys come only from environment variables.

### Safety

* No API key, private code, or private generated output may be committed (examples must come from projects the user has the right to publish; confirm before committing).
* Do not publish to PyPI, create tags, or push without explicit user authorization for that specific action.

### Local-first

* CI must not require any real LLM; the wheel job uses the stub server only.

---

# 7. Implementation Requirements

### Requirement 1 — Packaging

* `pyproject.toml`: `description`, `readme = "README.md"`, `license` (see Open Questions), `authors`, `keywords`, classifiers (Python 3.11–3.13, OS independent, Environment :: Console, Topic :: Software Development :: Documentation), `[project.urls]` (Homepage, Source, Issues), `requires-python = ">=3.11"`, version `0.1.0` (single source of truth chosen in task 01).
* `python -m build` produces sdist and wheel; **inspect the wheel** (`unzip -l`) and confirm templates and package data are included and tests/fixtures are not.
* Clean-environment smoke tests, recorded in the log: `uvx --from dist/<wheel> codewhy --version`; `pipx install dist/<wheel>` then `codewhy --version` and a scan against the stub server.
* `codewhy --version` matches the version in `pyproject.toml` and `CHANGELOG.md`.

### Requirement 2 — Stub Ollama server (`tests/support/stub_ollama.py`)

A stdlib `http.server`-based script: `--port`; implements `GET /api/version`, `GET /api/tags` (lists the configured model), `POST /api/chat` returning a schema-valid JSON string derived from the requested `format` schema with deterministic text. Logs nothing sensitive. Has its own unit test (`test_stub_ollama.py`) that starts it on a free port and exercises each endpoint. No production code imports it.

### Requirement 3 — CI (`.github/workflows/ci.yml`)

* Job `test`: matrix `os: [ubuntu-latest, macos-latest, windows-latest]` × `python: ["3.11", "3.12", "3.13"]`; `pip install -e ".[dev]"`; `scripts/check_wheels.py`; `pytest -q` (no `live_llm`). Fail-fast off so one OS failure does not hide others.
* Job `wheel` (needs `test`): build the wheel; in a clean venv, install **the wheel** (not the source tree); start `tests/support/stub_ollama.py` on a free port; run `codewhy scan tests/fixtures/flat_scripts --yes` (copy the fixture to a temp dir first) with a config pointing at the stub; assert exit 0 and that `PROJECT_DOCS.md` exists with the codewhy frontmatter; run `codewhy scan` again and assert the stub served zero `/api/chat` calls (cache works from the wheel). Run on all three OSes.
* Remove or fold `wheels-check.yml` (log which).
* Document in the README how to run the same checks locally.

### Requirement 4 — README

Structure (tight; a stranger gets a result in under five minutes):

1. One-sentence pitch and a short sample of real output (excerpt from `examples/`).
2. **Quickstart** with the three routes, each as copy-paste commands: **Gemini** (get a key, set `GEMINI_API_KEY`, `uvx codewhy scan . --provider gemini --model <id>` — state that model ids change and where to find the current one), **Ollama** (install, `ollama pull qwen2.5-coder:14b`, `uvx codewhy scan .`), and **other OpenAI-compatible endpoints** (config snippet). Then: "paste `PROJECT_DOCS.md` into your chat."
3. What you get (single file, sections, token budget and levels, docs-tree option).
4. Configuration reference (generated from or checked against `codewhy.toml`'s template) and `.toolignore`.
5. How it works (short; one diagram; deterministic facts vs model prose; cache behavior and its honest caveat).
6. **Privacy and data** section (see §6).
7. **Limitations** (see §6) and **FAQ** (e.g., "Why didn't it find my `@/` imports?", "Why was my file skipped?", "How do I re-run cheaply?", "Is the explanation always right?").
8. Exit codes and `--dry-run`.
9. Development: setup, tests, task/architecture docs pointer.

Every command in the README must be executed once by you from a clean checkout/environment; record which ones you ran.

### Requirement 5 — Release process

* `CHANGELOG.md` entry for `0.1.0`.
* Document in the log (and `CONTRIBUTING`-style section of the README if desired) the exact steps: re-verify the name is free; build; upload to **TestPyPI**; `uvx --index-url … codewhy --version` from TestPyPI; then PyPI. Prefer trusted publishing via `.github/workflows/release.yml` triggered by a version tag; if the user prefers manual `twine upload`, document that instead. **Do not publish or tag unless explicitly authorized.**
* If the PyPI name has been taken since the architecture date, stop and ask the project lead.

### Requirement 6 — Quality evaluation (`eval/`)

* `eval/corpus.toml`: the 3–4 projects (name, path or git URL + commit for open-source ones, license note, whether the generated output may be published).
* `eval/questions/<project>.toml`: 8–10 newcomer questions each ("Where is X handled?", "What happens if Y fails?", "Which file would I change to add Z?") with a **ground-truth answer written by the user from the code** (the agent may draft questions but must not invent ground truth; mark them `TODO(user)` if the user has not provided them and say so in the log).
* `eval/run_eval.py`: for each project and question, ask the configured provider (using `generate_json` with an `{answer: string}` schema) under three conditions — **(a)** file listing only, **(b)** raw concatenated source (skipped with a note when it does not fit the context budget), **(c)** the generated `PROJECT_DOCS.md`. Save raw answers to `eval/runs/<timestamp>/` (gitignored) and a scoring sheet with blank scores. Scoring (correct / partly / wrong) is done **by the user**, blind to condition where practical (shuffle and hide the condition label in the sheet).
* `eval/check_mentions.py`: extracts backticked identifiers and file paths from a generated document's LLM prose (not from deterministic sections) and reports the percentage that exist in the extraction output / project files, listing the misses. Reported as a metric, not a gate.
* `eval/RESULTS.md`: table of scores per condition per project, token size of each context, the mention-check percentages, and a short honest interpretation (including cases where the generated docs did worse than raw source or no better than a file listing).

### Requirement 7 — Demo artifacts

`examples/<project>/PROJECT_DOCS.md` for two projects (one small, one mid-size), each with the command and provider/model used and the date. Only projects the user confirms may be published.

### Error Handling

* Anything that cannot be done without user action (publishing, GitHub, scoring answers) is recorded **Not run (reason)** with the exact next step for the user. Never fabricate CI results, scores, or published links.

---

# 8. Testing Requirements

## Automated Tests

* `test_stub_ollama.py`: endpoints return the expected shapes; `/api/chat` honors the requested `format` schema; server shuts down cleanly.
* A packaging test (can run in CI): the built wheel contains the templates and entry point and excludes `tests/`.
* Existing suite must pass unchanged on the full CI matrix; no `live_llm` tests in CI.
* E2E (`live_llm`, run locally): all five fixtures per `docs/TESTS.md` → *E2E*: exit codes as specified, every parsed file has a section, frontmatter parses, no `[generation failed]` on healthy files, immediate re-run makes zero provider calls and is byte-identical, editing one file re-runs only what the cache rules predict.

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q -m live_llm
python -m build && unzip -l dist/*.whl
```

Expected result:

```text
offline suite green on the CI matrix; live E2E green locally; wheel contents correct
```

## Hardware / Model-Dependent Checks

Record each as Passed / Failed / Not run (reason) in `logs/09-ship.md`:

* CI matrix results (link or summary) for `test` and `wheel` jobs on all OS × Python pairs.
* `uvx --from <wheel> codewhy --version` and `pipx install <wheel>` smoke tests.
* README commands, each executed from a clean environment.
* The quality evaluation: scored results table, mention-check percentages.
* TestPyPI upload and install (if authorized).

## Manual Verification

1. Hand the README to someone who has never seen the project; time them from zero to a pasted `PROJECT_DOCS.md`. Record where they got stuck.
2. Run the tool on a project you have never read (an unfamiliar open-source repo) and judge whether the generated overview would have helped you understand it.
3. Re-read the README's limitations and privacy sections against the code and the task logs; delete any claim you cannot back.

---

# 9. Acceptance Criteria

* [ ] Wheel builds, contents verified, `uvx`/`pipx` smoke tests pass (or recorded Not run with reason).
* [ ] CI is green on the full matrix (or recorded Not run (no remote)); the wheel job proves install-and-scan from the built wheel against the stub, including a zero-call second run.
* [ ] README quickstart works for all three provider routes; every command was actually run; limitations and privacy sections are accurate and complete per §6.
* [ ] Quality evaluation executed; results table written honestly, including unflattering results.
* [ ] Demo artifacts committed only for projects cleared for publication.
* [ ] Release steps documented; nothing published or tagged without authorization; the PyPI name re-verified.
* [ ] No secrets, private code, or private outputs in the repository.
* [ ] `git diff` reviewed; work log updated.
* [ ] If a commit was authorized: staged diff reviewed, only intended files, no AI attribution, no push/PR.

---

# 10. Known Risks

* **The evaluation may show the tool is no better than a file listing for some projects.** That is a finding, not a failure to hide; record it, and note which prompt or design change would address it (v2 or a `PROMPT_VERSION` bump if it is a bug).
* **Free-tier and API behavior drift** (model ids, rate limits, parameters) can break the Gemini quickstart between writing and publishing. Date-stamp the README example and keep the model id out of defaults.
* **Windows CI will find things nobody else has** (paths, encodings, symlink permissions). Budget time to fix; fixes belong to the owning code, with the relevant test added.
* **Scoring is subjective.** Blind it where possible and say how many questions and projects it covers; do not generalize beyond that sample.
* **Publishing under the wrong name or with a typo is hard to undo** (PyPI does not allow re-uploading a version). Always TestPyPI first.
* **Example outputs can leak private code** through explanations. Review every generated example before committing.

---

# 11. Open Questions

* **requires clarification:** License? — Recommended: MIT (permissive, typical for dev tools); the user decides.
* **requires clarification:** Trusted publishing workflow or manual `twine`? — Recommended: trusted publishing triggered by a version tag; document the one-time PyPI configuration.
* **requires clarification:** Which projects form the evaluation corpus, and which may be published as examples? — Recommended: two own projects of different shapes plus one small unfamiliar open-source project; publish examples only for projects with a permissive license or ones the user owns.
* **requires clarification:** Which Gemini model id appears in the README example? — Recommended: whatever is current at release time, clearly dated, with a pointer to Google's model list.

---

# 12. Handoff Notes

* After this task the project is ready for a first public release. Remaining v2 candidates (from the architecture's deferred list and the evaluation): Layer 5 grounding (identifier-existence check first), secret redaction, `tsconfig` alias resolution, README-aware understanding, real tokenizer counting.
* Record in the log: CI links/results, wheel smoke-test results, evaluation table and its caveats, README command verification list, release steps taken or pending, and any `PROMPT_VERSION` bump with the reason.

If the task is complete:

> No outstanding handoff notes.

---

# 13. Definition of Done

See `CLAUDE.md` → *Definition of done*. Do not commit, push, tag, publish, open a PR, or merge unless the user explicitly authorizes that specific action.

## Final Agent Instruction

Before making changes: read `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/TESTS.md`, this file, and the logs of tasks 01–08; run `git status`; write your plan into `logs/09-ship.md`.

During implementation: never invent ground truth, scores, or CI results; stay in scope; verify every README command; do not publish anything.

If the task requires something not covered here, stop and ask the project lead.

Before handing off: run the tests and what you can of the live and packaging checks (record the rest as Not run with the exact next step), review `git diff` and `git status`, update the log, and report what changed, what was verified, and what is untested or unresolved.
