# codewhy quality evaluation

The question: does a newcomer get better answers about a codebase from a chat
model when it has `PROJECT_DOCS.md`, compared with just a file listing or the
raw source?

## Pieces

| File | What it is |
|---|---|
| `corpus.toml` | the projects, with where they live and whether their output may be published |
| `questions/<project>.toml` | 8–10 newcomer questions per project, each with a `ground_truth` written **by a person from the code** |
| `run_eval.py` | asks every question under three contexts and writes a blind scoring sheet |
| `check_mentions.py` | checks that backticked names in the model's prose exist in the project |
| `RESULTS.md` | the scored results and their interpretation |
| `runs/` | raw answers and sheets (gitignored, never committed) |

The three conditions use the same question and model; only the context changes:

- **a**: the file listing only;
- **b**: the raw concatenated source (skipped with a note when it does not
  fit the model's prompt budget);
- **c**: the generated `PROJECT_DOCS.md`.

## Running it

1. **Ground truth first.** Replace every `ground_truth = "TODO(user)"` in
   `questions/<project>.toml` with the correct answer, written from the code.
   Do not use the generated docs or a model for this.
2. **Generate the docs** used as condition c (the path is `docs` in
   `corpus.toml`). For codewhy, from the repository root:

   ```bash
   rm -rf /tmp/cw-eval && mkdir -p /tmp/cw-eval && cp -R src/codewhy /tmp/cw-eval/codewhy
   find /tmp/cw-eval -name __pycache__ -prune -exec rm -rf {} +
   codewhy scan /tmp/cw-eval/codewhy --provider gemini --model <id> --yes
   mkdir -p examples/codewhy && cp /tmp/cw-eval/codewhy/PROJECT_DOCS.md examples/codewhy/PROJECT_DOCS.md
   ```

   The copy keeps codewhy's cache and output out of the source tree. The
   document title comes from the folder name, which is why the copy is still
   named `codewhy`.
3. **Ask:**

   ```bash
   .venv/bin/python eval/run_eval.py --project codewhy --provider gemini --model <id> --yes
   ```

   It prints the number of calls and estimated input tokens first.
   `--yes` is required for remote endpoints: the project's source and docs are
   sent there.
4. **Score blind.** Open `runs/<timestamp>/sheet.csv`. For each row, compare
   `answer` with `ground_truth` and write `correct`, `partly` or `wrong` in
   `score`. The rows are shuffled and do not show the condition; do not open
   `key.csv` until you are done.
5. **Summarize:**

   ```bash
   .venv/bin/python eval/run_eval.py --summarize eval/runs/<timestamp>
   .venv/bin/python eval/check_mentions.py src/codewhy examples/codewhy/PROJECT_DOCS.md
   ```

   Copy both outputs into `RESULTS.md` with an honest interpretation.

## Caveats

- One project, 10 questions, one scorer: a small sample. Do not generalize.
- The scorers wrote codewhy, so they know the answers well. A stranger's
  project would test the tool better; add one to `corpus.toml` when one is
  cleared.
- The mention check counts names that exist. A name that exists can still be
  described wrongly, and a CLI flag or placeholder written in backticks
  counts as a miss. Read the miss list.
