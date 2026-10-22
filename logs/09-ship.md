# CW-09 — Ship it: packaging, CI, README, evaluation, release

Branch: `task/09-ship`, created from `task/08-cli-robustness` (commit
`825e456`).

## 2026-10-22 — lead decisions (asked at the start)

| Question | Answer |
|---|---|
| License | **MIT** |
| Live model for E2E, examples and evaluation | **Gemini** (the lead provides the key; nothing about it is logged or committed) |
| Projects that may be published and used for the evaluation | **codewhy only** (the tool's own repository, including its own test fixtures) |
| Release route | **Trusted publishing** via a tag-triggered `release.yml` |

PyPI name re-verified 2026-10-22: `https://pypi.org/pypi/codewhy/json` and
`https://test.pypi.org/pypi/codewhy/json` both return **404**, so the name is
free on both.

## Plan

1. Packaging:
   - `LICENSE` (MIT);
   - `pyproject.toml` metadata (license, authors, keywords, classifiers,
     URLs);
   - `CHANGELOG.md` 0.1.0.
2. `tests/support/stub_ollama.py` (stdlib only): `/api/version`,
   `/api/tags`, `/api/chat` honoring `format`, plus a `/stub/stats` call
   counter for the CI zero-call assertion. Tested by
   `tests/test_stub_ollama.py`.
3. Wheel checks:
   - `tests/test_packaging.py` inspects a built wheel named by
     `CODEWHY_WHEEL` (skipped when unset, so the default suite stays
     offline);
   - `scripts/wheel_smoke.py` runs install-and-scan against the stub (used
     by CI and locally).
4. CI and release:
   - `.github/workflows/ci.yml`: `test` matrix plus `wheel` job on 3 OSes;
     `wheels-check.yml` folded into it and deleted;
   - `.github/workflows/release.yml`: trusted publishing, TestPyPI by manual
     dispatch, PyPI on a `v*` tag.
5. `eval/`:
   - corpus (codewhy);
   - drafted questions with `ground_truth = "TODO(user)"`, because the agent
     must not invent ground truth;
   - `run_eval.py` (3 conditions, blind scoring sheet);
   - `check_mentions.py`;
   - `RESULTS.md`.
6. `README.md`: full rewrite per Req. 4.
7. Live work with Gemini (once the key is available to this session):
   - extended live E2E over all five fixtures;
   - examples for `codewhy` (mid-size) and its `flat_scripts` fixture
     (small);
   - evaluation answers;
   - mention check;
   - README commands.
8. Local smoke tests: `uvx --from <wheel>` and an isolated `pipx install`
   (`PIPX_HOME`/`PIPX_BIN_DIR` in the scratchpad).

## Decisions and deviations

- **Files outside the allowed list:**
  - `tests/test_packaging.py` and `scripts/wheel_smoke.py` exist because §8
    requires a packaging test and §7 Req. 3 requires the wheel job's
    install-and-scan. A Python driver keeps that job identical on Windows.
  - `.gitignore` gains `/eval/runs/`, because Req. 6 says raw answers are
    gitignored.
- **No new dependency for building:**
  - CI and the release workflow install `build` themselves as tooling; it is
    not a project dependency.
  - Locally the wheel is built with `uv build` (already installed).
- **Examples:** "codewhy only" gives one mid-size project. The small example
  is the repository's own `tests/fixtures/flat_scripts`, which is part of
  codewhy and covered by the same answer.
- **Evaluation corpus:** one project instead of the 3–4 the task suggests,
  per the lead's answer. `RESULTS.md` must say so and not generalize.

## Progress (offline work, 2026-10-22)

- **Packaging:**
  - `LICENSE` (MIT); `CHANGELOG.md` (0.1.0, unreleased).
  - `pyproject.toml`: PEP 639 `license = "MIT"` (build backend
    `hatchling>=1.27`), authors, keywords, classifiers 3.11–3.13,
    `[project.urls]`.
  - The sdist is limited to `src/`, `tests/`, `docs/ARCHITECTURE.md`,
    `docs/TESTS.md`, README, LICENSE, CHANGELOG and the example config.
    Before this it shipped `members/`, `old_docs/`, `logs/` and `CLAUDE.md`.
- **`uv build`:** **Passed.** The wheel has 48 entries: all `codewhy/*.py`,
  both templates, entry point, `licenses/LICENSE`; no tests, docs or scripts.
  `CODEWHY_WHEEL=dist/codewhy-0.1.0-py3-none-any.whl pytest -q
  tests/test_packaging.py`: 4 passed.
- **Clean venv** (uv, Python 3.12) `pip install <wheel>`, then
  `scripts/wheel_smoke.py`: **Passed.** `codewhy 0.1.0`; scan exit 0 with
  frontmatter; 5 `/api/chat` calls; second scan made 0 calls and its output
  was byte-identical.
- **`uvx --from dist/codewhy-0.1.0-py3-none-any.whl codewhy --version`:**
  **Passed** (`codewhy 0.1.0`).
- **`pipx install <wheel>`:** **Passed**. pipx ran via `uvx pipx`, isolated
  with `PIPX_HOME`/`PIPX_BIN_DIR` in the scratchpad so nothing was installed
  on the machine. `codewhy --version` = 0.1.0, and `wheel_smoke.py` against
  that pipx install passed.
- **Stub Ollama:**
  - `tests/support/stub_ollama.py` (stdlib only; `/stub/stats` counts chat
    calls).
  - `tests/test_stub_ollama.py`: 7 passed, including a real `codewhy scan`
    through the Ollama provider against the stub, and a zero-call re-run.
- **CI:**
  - `.github/workflows/ci.yml`: `test` 3 OS × 3.11/3.12/3.13, fail-fast off,
    `check_wheels.py`, then `pytest -q`. `wheel` (needs `test`, 3 OS): build,
    clean venv, install the wheel, `test_packaging.py`, `wheel_smoke.py`.
  - `wheels-check.yml` is **deleted**; its grammar check is the first step of
    `test`.
  - Both workflows parse as YAML.
  - **CI itself: Not run (no push authorized).** Next step for the lead: push
    the branch and read the Actions results.
- **Release:** `.github/workflows/release.yml` uses trusted publishing.
  Manual dispatch goes to TestPyPI; a `v*` tag goes to PyPI, and the build
  job checks that the tag matches `__version__` and that CHANGELOG has the
  entry. The one-time PyPI/TestPyPI and GitHub-environment setup is written
  in the README under *Releasing*. Nothing has been published or tagged.
- **Live E2E:** `tests/test_e2e_live.py` is extended:
  - all five fixtures, with an immediate zero-generation, byte-identical
    re-run each;
  - an edit-one-file cascade check on `nested_modules`;
  - `CODEWHY_LIVE_PROVIDER` / `CODEWHY_LIVE_MODEL`.

  The test logic was validated against the stub server on port 11434: 7
  passed. That run is **not** a live result; the real-model runs are below.
- **Evaluation:**
  - `eval/` contains README, `corpus.toml` (codewhy = `src/codewhy`), 10
    drafted questions with `ground_truth = "TODO(user)"`, `run_eval.py`
    (conditions a/b/c, shuffled sheet plus a separate key, `--summarize`)
    and `check_mentions.py`.
  - Context sizes, Gemini preset: listing ≈ 239 tokens; raw source ≈ 73,713
    tokens against a prompt budget of 22,118, so condition b is skipped with
    a note.
- **README:** rewritten per Req. 4. The sample excerpt is pending the Gemini
  example.
- Full offline suite: 821 passed, 11 skipped (7 live_llm, 4 packaging without
  `CODEWHY_WHEEL`).

## 2026-10-22 — first commit of task 09 (authorized by the lead)

- Added `.env.example` (committed template). The lead keeps the Gemini key in
  `.env`, which `.gitignore` already ignores (verified with `git
  check-ignore`). codewhy does not read `.env`; commands load it with
  `set -a; . ./.env; set +a`.
- This commit holds the offline work above. Still pending: live E2E,
  `examples/`, the README sample excerpt, evaluation answers and
  `RESULTS.md`, plus the lead's ground truth and scoring.
- Secret scan of the staged diff (Google/OpenAI key patterns, filled
  `GEMINI_API_KEY=`): nothing found.

## 2026-10-22 — first CI run (GitHub Actions run 39730795579)

- **`test` job:**
  - ubuntu and macOS, Python 3.11/3.12/3.13: **Passed** (all 6).
  - windows, Python 3.11/3.12/3.13: **Failed**. Two test portability bugs
    of mine, not code defects; 812–813 of 814 tests passed.
    1. `test_atomic_write.py::test_docs_tree_writes_nothing_when_any_overwrite_check_fails`
       looked for `codewhy-docs/main.py.md`. The error message correctly
       names the absolute OS path, which on Windows uses backslashes. The test
       now builds the expected text with `Path`.
    2. `test_stub_ollama.py::test_script_starts_reports_its_port_and_stops_cleanly`
       (3.11, 3.12) expected `ConnectError` once the stub stopped. Windows
       retries a closed localhost port for about 2 s, so the 2 s request
       timed out instead. Both errors now count as "server gone".
- **`wheel` job:** **Not run** (skipped because `test` failed). It runs on
  the next push.
- Local suite after the fix: 821 passed, 11 skipped.
