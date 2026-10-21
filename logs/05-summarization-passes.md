# CW-05 — Summarization passes: work log

Branch: `task/05-passes` (from `main` at 1767ccd). Started 2026-10-21.

## Plan

Read first: `CLAUDE.md`, `docs/ARCHITECTURE.md` (Layer 4, Layer 5, failure
boundaries), `docs/TESTS.md` (budgeting, Layer 4, chunking, Layer 5), the task
file, and the logs of tasks 01–04.

Build order (each step tested before the next):

1. `text.py` (`first_sentence`) and `summarize/result.py` (`PassResult`,
   `call_pass` with retry-once, `PassFailed`, `RETRY_TEMPERATURE`, the
   retry-safe pass budget).
2. `summarize/prompts.py`: `PROMPT_VERSION`, `render_structure`, the file /
   chunk / combine / module / project templates, `retry_suffix`.
3. `llm/chunking.py` (`plan_chunks`) and `tests/fixtures/builders.py`
   (`build_oversized_file`).
4. `summarize/file_pass.py`: empty-file shortcut, fit rule, chunk + combine.
5. `summarize/module_pass.py` and `summarize/project_pass.py`: ladders, split
   into a pure "assemble" step and an "execute" step.
6. `pipeline.py`: `run_pipeline`, the `run_pass` seam, counts, `on_item`.
7. `scripts/dev_run_passes.py`.
8. Tests listed in task §8, then the budget-invariant property test.
9. Live checks with local Ollama (Gemini needs the lead's key and credits).

## Decisions

Open questions in task §11, both taken as recommended:

- The combine step sees chunk **Purpose text only** (no notes), matching the
  cascade rule.
- The minimum useful chunk size stays at **500 estimated tokens**
  (`chunking.MIN_CHUNK_TOKENS`). Revisit only if real projects hit it.

Decisions later tasks depend on (also written into `docs/ARCHITECTURE.md`
where they are contract details):

- **Retry reserve.** Retry prompts must fit the budget too (the invariant covers
  every prompt sent). Passes assemble against `pass_budget(config)` =
  `prompt_budget − RETRY_RESERVE_TOKENS`. The reserve is the estimate of
  `retry_suffix` with a 500-character error, and error text is capped at 500
  characters, so prompt + suffix always fits (ceil is subadditive). `call_pass`
  also refuses to send any prompt over the pass budget, as a last guard.
- **Nothing fits → "context too small", never a call.** Applies to files
  (chunk room < 500, or the combine prompt still overflows after cutting to
  first sentences), modules and the project (ladder step 3 with no items and
  the edge list cut to zero lines still overflows).
- **Ladder step 3** uses first-sentence texts (step 2 already failed with
  them), takes the longest prefix of the in-degree order that fits, and lists
  the kept items by path. The omitted line is literally
  `N more files omitted from this prompt` / `N more modules …` (plural even
  for 1, as the architecture writes it). Edges are cut only when the prompt
  does not fit even with no items.
- **Module-to-module edges** for the project prompt drop edges inside one
  module (they are not module-to-module).
- **Combine prompt** carries the top-level names list, not the full structure
  listing. For an oversized file the listing can itself be thousands of tokens.
- **Chunk overhead** is measured on a placeholder chunk with the largest line
  numbers and a text as long as the file's longest backtick run (which sets the
  code-fence length). Any real chunk prompt is then ≤ overhead + the chunk's
  own estimate.
- **Line splitting** for chunks is on `\n` only (`source_lines`), to match
  parser line numbers. `str.splitlines` would also split on form feed and
  U+2028.
- **Chunk planner robustness:** gaps or overlaps in `top_level_units` are
  absorbed (a gap joins the following unit), so no line is dropped or
  repeated even if extraction misbehaves.
- **Truncation** keeps the longest head that fits (binary search; the size
  only grows with the head) and always omits at least one line.
- **Class split:** the header (lines before the first method's range) is
  merged with the first method when both fit together, otherwise it is its
  own piece. Greedy packing may also put it after the previous unit in the
  same chunk.
- **Skipped files** (`status == "skipped"`) count in `counts["skipped"]` and
  appear in their module's prompt as `(summary unavailable)`. Discovery skips
  (too large / undecodable) are not in `ProjectModel.files`, so they are not
  counted here; they are in `project.skips` for tasks 06/08.
- **Empty-file shortcut** counts as `generated` and reports `on_item(...,
  "generated")`.
- **`call_pass` and `PassFailed`** live in `summarize/result.py` (the allowed
  list has no separate helper file). Failure reasons never contain model
  text: validation errors are reported as `field: message` only.
- **The project subject** passed to `run_pass`/`on_item` is `""`, matching the
  cache's subject for the project row.

### For task 07 (cache) and task 08 (dry-run)

- Module and project passes are split into a pure **assemble** step
  (`assemble_module_prompt`, `assemble_project_prompt` → `AssembledPrompt(prompt,
  inputs, failure)`) and an **execute** step (`module_pass.execute`). The
  pipeline assembles first, then calls `run_pass(pass, subject, assembled.inputs,
  fn)`, so `inputs` is known before any call. Task 08's dry-run can call the
  assemble functions (and `file_prompt`/`plan_chunks`) without a provider.
- `run_pass` inputs:
  - file: `{path, source_text, imports, imported_by}`;
  - module: `{module, files: [[path, text]…] post-ladder, omitted, edges (as
    sent, possibly cut), external}`;
  - project: `{root_name, entry_point, modules post-ladder, omitted,
    module_edges (as sent), other_files (capped list as sent)}`.
  The architecture's project key also includes the **full canonical project
  edge list**; task 07 must add it, since the prompt only carries the
  module-to-module summary.
- **Naming mismatch to resolve in task 07:** the task calls the constant
  `PROMPT_VERSION` (in `summarize/prompts.py`), while the architecture's cache
  key says `PIPELINE_VERSION` (bumped for prompts, schemas, extraction output
  or chunking). Not changed here.
- **How to bump `PROMPT_VERSION`:** increase it by 1 by hand in
  `summarize/prompts.py` whenever any template text there, any field
  description in `summarize/schemas.py`, or the chunking behavior changes, and
  note the change in this log's version history.

### `PROMPT_VERSION` history

| Version | Date | Change |
|---|---|---|
| 1 | 2026-10-21 | Initial templates. |

## Commands run

- `.venv/bin/python -m pytest -q`: **633 passed** (Python 3.14.7; 398 before
  this task). New tests: prompts 30, file_pass 15, chunking 41, module_pass 7,
  project_pass 8, retries 16, pipeline 11, budget_invariant 107.
- Budget invariant: 5 projects (`flat_scripts`, `nested_modules`,
  `python_js_mixed`, `oversized_file`, plus a built 40-file `wide_module`) × 7
  context sizes (16384 down to a tiny 600) × 3 model behaviors (well-formed;
  malformed first attempt so every prompt is also sent as a retry; verbose
  near-cap answers so module/project prompts climb the ladder). Every prompt
  was within `prompt_budget`; at 600 tokens nothing is sent and every
  non-empty item fails with "context too small".
- Mutation checks (each reverted afterwards; the source was compared byte for
  byte with a backup in the scratchpad). Each mutant made the suite fail:
  - no retry reserve;
  - no retry;
  - retry keeps the original temperature;
  - no first-sentence fallback in combine;
  - no class split;
  - off-by-one omitted-line count;
  - ladder ignores in-degree;
  - project prompt without the no-entry-point instruction;
  - chunk overhead measured on a minimal placeholder.
- `scripts/dev_run_passes.py tests/fixtures/flat_scripts --provider gemini
  --model x` (no key set): refuses, exit 1, nothing sent.
- `scripts/dev_run_passes.py tests/fixtures/flat_scripts --model
  qwen2.5-coder:7b` with Ollama stopped: `Cannot reach Ollama … ollama serve`,
  exit 1.
- `git diff --check`: clean. No `print` in library code.

## Live / model-dependent checks (task §8)

| Check | Result |
|---|---|
| `dev_run_passes.py` on `flat_scripts` with real Ollama | **Not run.** The lead declined starting the Ollama server from this session. Command below. |
| Same on one of the lead's own projects | **Not run** (needs a model). |
| Forced-chunking run (small `llm.context_tokens`, oversized fixture, real model) | **Not run** (needs a model). |
| Same with Gemini | **Not run** (needs the lead's key and account credits; see task 04's log). |

To run them in your own terminal:

```bash
ollama serve                       # in another terminal, if it is not running
.venv/bin/python scripts/dev_run_passes.py tests/fixtures/flat_scripts --model qwen2.5-coder:7b > flat.json
# forced chunking: build the oversized fixture into a scratch folder, then shrink the context
.venv/bin/python -c "from pathlib import Path; from tests.fixtures.builders import build_oversized_file; print(build_oversized_file(Path('/tmp/cw05'), chunk_tokens=600))"
printf '[llm]\ncontext_tokens = 2400\nmax_output_tokens = 256\n' > /tmp/cw05/oversized_file/codewhy.toml
.venv/bin/python scripts/dev_run_passes.py /tmp/cw05/oversized_file --model qwen2.5-coder:7b > chunked.json
```

Record calls, failures, seconds and whether each file Purpose explains *why*
the file exists (who uses it) rather than listing functions.

## Manual verification (task §8)

1. Real-model Purposes for `flat_scripts`: **Not run** (no model; see above).
   Prompt iterations, if any, go in the `PROMPT_VERSION` history.
2. Prose with fences / missing field: **Passed** offline. `test_retries.py`
   covers unparseable output, a missing field and an empty required string;
   each retries once and then becomes a failed item while the run continues.
   Fenced JSON is parsed by the providers' `extract_json_object` (task 04
   tests).
3. Provider calls for a 15+-file project: **Passed** offline. `nested_modules`
   (17 files, 7 modules) makes exactly 17 + 7 + 1 = 25 calls
   (`test_one_call_per_file_module_and_project`); with an empty `__init__.py`
   the count drops by one (`test_skipped_files_…`).

## Deviations / files outside the allowed list

- `docs/ARCHITECTURE.md` (allowed): budgeting (retry reserve, "context too
  small"), chunking (combine fallback, names list, 500-token minimum), ladder
  step 3 details and edge cutting, module-to-module edge definition, the
  other-files cap of 50 in the prompt.
- No other files outside the list. `schemas.py` unchanged.
- The branch is named `task/05-passes`, as the task suggests.

## Open issues

- Live quality checks above are required before this task is called done
  (task §9: "Real-model output … explains purpose, not just content").
- `PROMPT_VERSION` vs `PIPELINE_VERSION` naming (task 07).
- The field description for `notes` begins with "Optional:", so the prompt
  reads `"notes" (optional): Optional: …`. This is harmless; tidy it only with
  a `PROMPT_VERSION` bump.
