# Review log — CW-06 rendering and scan wiring

Branch: `review/06-render-scan`, created from `task/06-render-scan`
(Shregna, commit `2c48f8a`, based on `main` at `6f4e9fa` with CW-01–CW-05
merged). Reviewer: Aditya (lead). The author's log,
`logs/06-render-and-scan.md`, is left unchanged.

## 2026-10-21

### Review findings (before any change)

Checked against `docs/ARCHITECTURE.md` (documentation contract, Output, CLI
surface, failure boundaries), `docs/TESTS.md` (Rendering, CLI, E2E), and
`tasks/06-render-and-scan.md`.

- Baseline: `pytest -q` **1 failed, 657 passed, 1 skipped** (Python 3.14.7, macOS).
- Works as specified (verified by running it with `FakeProvider`):
  - single-file, docs-tree and `both` output;
  - exit codes 0/1/2;
  - hand-written `PROJECT_DOCS.md` refused, `--force` overwrites;
  - an output path outside the root is rejected;
  - consent and preflight run before any source read, and a remote provider
    without `--yes` makes zero calls;
  - a second scan does not list its own output under "Other files".

Defects found:

1. **Red suite, test hits the network.**
   `test_inspect.py::test_scan_reports_missing_explicit_entry_point_cleanly`
   ran a real `scan`, so it contacted Ollama on localhost. It passed on the
   author's machine (Ollama running) and fails anywhere else. This breaks the
   CLAUDE.md rule that tests outside E2E run offline.
2. **Model text could break document structure** (§6: "Markdown-injection
   attempts … never alter structure"). Only `#` in column 0 was escaped.
   Reproduced with a scripted model answer:
   - an unclosed fence ("```") swallowed the rest of the document into a code
     block;
   - `   # x` (indented) and `Text` + `---` (setext) became real headings.

   The existing test fed in an unclosed fence but never checked structure, and
   `test_escape_llm_text…` asserted that "```" passes through unescaped.
3. **Out-of-scope concurrency in the pipeline.** File and module passes ran on
   two threads for providers that opt in (all OpenAI-compatible ones,
   including Gemini). The architecture was not updated, task 05 specifies
   sequential execution, and task 08 lists parallelism out of scope.
   Consequences, reproduced:
   - an `AuthError` on the first call still let 16 more calls go out on
     `nested_modules` (the contract says auth/bad-request abort the run);
   - `on_item` progress lines were printed only after the whole file phase,
     for every provider, including serial Ollama (the author's log says item
     progress was "unchanged");
   - task 07's cache seam (`run_pass`) would run on worker threads with SQLite.
4. **Required items missing:**
   - no golden-update helper (Req. 6);
   - the docs-tree golden covered only `PROJECT_OVERVIEW.md`;
   - goldens used hash-text fake answers, so Notes were never frozen;
   - about half of §8's tests were absent: Notes omitted, Other-files cap,
     `generated_at` = latest result, symlinks never followed, `--force` and
     silent overwrite, summary line content, self-pollution, `.gitignore` not
     read, nothing written outside the root, `(project root)`, CRLF;
   - the live E2E asserted less than §8 asks.
5. **Layout deviations frozen in goldens:**
   - internal dependencies rendered `` `a → b` `` instead of
     `` `a` → `b` ``;
   - Level 2 modules kept a `#### Responsibility` heading (spec: the
     paragraph only).
6. **Task 04 contract changed silently:** preflight timeout became a fixed
   10 s instead of `min(timeout_seconds, 10)`.
7. **Deleted tests that should have been adapted:** `scan --provider/--model`
   acceptance, and scan surviving malformed metadata.

### Fixes (this branch)

1. `test_inspect.py`: the scan tests use `FakeProvider`; the malformed-metadata
   test covers `scan` again.
2. `render/sections.py` `escape_llm_text`:
   - trim first, then per line put one backslash before a block-opening
     character after up to 3 spaces: `#`, ``` ` ```/`~` fences, `<` HTML
     blocks;
   - same for lines made only of `= - * _` (setext underline or rule);
   - text otherwise unchanged, so `Issue #12` stays as is.

   `docs/ARCHITECTURE.md` renderer rule updated to say exactly this. New test:
   a hostile answer (headings, setext, unclosed fence, unclosed comment) yields
   the same heading structure as a benign one at Levels 0/1/2, using a small
   CommonMark-style heading scanner that honours fences.
3. Concurrency removed: `pipeline.py` is sequential again and records each item
   as it finishes; `supports_concurrency` was removed from both providers and
   `FakeProvider`, and `tests/test_pipeline_concurrency.py` deleted. Kept: the
   author's stage/request progress callbacks (`on_stage`, `on_request`) and the
   "checking provider …" line; see open issues. New tests: progress lines
   interleave with calls (`call, item` × 5), and an `AuthError` stops the run
   after exactly one call.
4. Goldens rebuilt from scripted, readable answers (one file has Notes),
   reviewed by eye against task §7 Req. 1, and the docs-tree golden is now the
   whole tree (`tests/golden/flat_scripts_tree/`, 5 files). Update procedure:
   `CODEWHY_UPDATE_GOLDEN=1 .venv/bin/python -m pytest -q
   tests/test_render_single_file.py tests/test_render_docs_tree.py`, then
   review `git diff tests/golden` and log it. Missing §8 tests added; test
   counts now: sections 20, single-file 10, docs-tree 10, scan 22 (12 + 10). Live E2E now checks
   frontmatter, Level 0 within the default budget, section order, and a real
   Purpose for every file.
5. Layout: `` - `a` → `b` `` for internal dependencies; Level 2 modules are
   `### path` + the Responsibility paragraph.
6. Preflight timeout back to `min(timeout_seconds, 10)`; the author's clearer
   timeout messages are kept and now name the actual value.

### Verification

- `pytest -q`: **693 passed, 1 skipped** (the skip is the live E2E).
- Mutation checks, each reverted (the source was compared byte for byte with a
  scratchpad backup). Every one failed the suite:
  - escape only column-0 `#`;
  - no setext/rule escape;
  - old internal-deps format;
  - file items not recorded as they finish;
  - level budget `<` instead of `<=`.
- `git diff --check`: clean.

### Live / manual checks (task §8)

| Check | Result |
|---|---|
| Live E2E `pytest -m live_llm tests/test_e2e_live.py` | **Not run** (no model started in this session; the author's run timed out with the 14B model) |
| `scan` on two real projects | **Not run** |
| Paste test (three questions to a chat model) | **Not run** |
| Stranger read / repeat-scan diff / hand-written refusal by hand | Refusal and `--force` **Passed** (scripted); the others **Not run** |

### Open issues for the lead

- The live checks above are acceptance criteria; task 06 is not done until
  they are recorded.
- The author's stage and per-request progress lines (`discovery: started`,
  `file request X: started: attempt 1/2; prompt ~N tokens …`) and the Ctrl+C →
  exit 130 handling are task 08 scope. They are harmless and help with slow
  local models, so they were kept rather than deleted; task 08 should own or
  replace them.
- Speed on large projects (the author's reason for the concurrency) is
  addressed by task 07 (cache, resume). If parallelism is wanted later, it
  needs an architecture decision first (abort semantics, cache thread
  safety, rate limits).
- "Other files in this project" is omitted when there are none (the spec
  only names Notes and Most depended-on as omittable). Harmless; left as is.
- Empty lists render as `- None` (a bullet). The spec's "(or: None)" is
  ambiguous; left as the author wrote it.

## 2026-10-22 — remaining checks after the merge

`main` (`7bb53ee`) merged this review with the live checks still open. They
continue on `review/06-07-render-cache` together with the CW-07 review
(`logs/review-07-cache.md`). The code was re-read against task §6–§8. No new
code defects were found beyond the earlier review, which the merge includes.

Plan:

1. Let the live E2E pick a model through an opt-in `CODEWHY_LIVE_MODEL`
   environment variable. The test was hardwired to the default
   `qwen2.5-coder:14b`, which is not pulled on this machine. Add the
   TESTS.md E2E re-run check: zero generated and byte-identical output.
2. Run the live E2E on `flat_scripts` with local Ollama `qwen2.5-coder:7b`.
3. Scan two real projects (copies in the scratchpad; nothing written into
   the originals). Record counts, failures, level and token estimate.
4. Paste test: give one `PROJECT_DOCS.md` cold to a fresh model and ask three
   questions. Record whether the answers were right.

### Results (2026-10-22)

- `tests/test_e2e_live.py` now takes an optional `CODEWHY_LIVE_MODEL`
  (passed as `--model`). After the first scan it re-runs and asserts 0
  generated and byte-identical output.

| Check | Result |
|---|---|
| Live E2E `CODEWHY_LIVE_MODEL=qwen2.5-coder:7b pytest -q -m live_llm tests/test_e2e_live.py` | **Passed** (1 passed, 21.7 s, local Ollama) |
| `scan` on real projects | **One of two run** (lead's instruction: one project only, for time). Sudoku Solver: 9 files, 12 generated, 0 failed, 0 skipped, level 0, ≈ 3,574 tokens. The second project was **Not run**. |
| Paste test | **Not run** (time; needs a person to judge the answers) |
| Repeat-scan diff | With the CW-07 cache the re-run is byte-identical (above). |

Task 06 remains short of its acceptance criteria on the second real project
and the paste test.
