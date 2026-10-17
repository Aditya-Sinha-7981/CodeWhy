# codewhy — Sheikh's plan

You own repo setup, the provider layer (task 04), `scan` wiring (task 06's wiring half), CLI robustness (task 08), and the CI/packaging/stub-server/README-commands work in task 09. Read `CLAUDE.md`, `docs/ARCHITECTURE.md` (especially *Provider interface*, *Privacy and remote endpoints*, *CLI surface*, *Failure boundaries*), `docs/TESTS.md`, and `tasks/ALL-TASKS.md` first.

## Your tasks, in order

| # | Starts when | Branch | You deliver | Reviewer | Waiting on you |
|---|---|---|---|---|---|
| Repo setup | Now | — | GitHub repo with Actions on, branch protection on `main`, secret scanning on if available | Aditya | Everyone (Shregna needs the remote for the wheel matrix) |
| 04 | Aditya merges 01 | `task/04-providers` | Ollama, openai-compat, `gemini` preset, budget, schemas, consent, `doctor`, `FakeProvider`, smoke script | Aditya (key handling) + Shregna | Aditya's 05 |
| 09 (early items) | While 05 is in progress | `task/09-stub-ci` | `tests/support/stub_ollama.py` + test, `ci.yml` test-matrix job, packaging metadata draft | Aditya | — |
| 06 (wiring) | 05 merged and Shregna's render is merged | `task/06-scan-wiring` | Real `scan`: consent, preflight, pipeline, render, summary line, exit codes, integration tests (Requirement 5 of `tasks/06-render-and-scan.md`) | Shregna | Aditya's 07 integration |
| 08 | 07 is merged | `task/08-cli-robustness` | `--dry-run`, progress, exit-code audit, atomic writes, interrupt handling, edge cases (everything in `tasks/08-cli-robustness.md` except Requirement 4, the matrix test) | Aditya | Shregna's matrix test |
| 09 (late items) | 08 merged | `task/09-ci-packaging` | Wheel CI job, wheel smoke tests (`uvx`, `pipx`), README commands verified | Aditya | Release |

The 09 items under "early" are deliberately pulled forward because they depend on nothing but the repo; record them in `logs/09-ship.md` when you do them.

## Before you can start (do these now)

1. **Repo setup:** create the GitHub repo, enable Actions, protect `main` (PR required, review required, status checks required once CI exists), and turn on secret scanning/push protection if your plan offers it. Add the doc set: `CLAUDE.md`, `docs/`, `tasks/`, `team/`.
2. **Provider research for task 04:** get a free Gemini key from Google AI Studio and install Ollama with the default model (or note that your laptop can't). Read the current Ollama API docs and Google's OpenAI-compatibility page. Note the version and URL of everything you rely on.
3. **Gemini live check is a real deliverable in 04.** No one has verified yet which parameters the compat endpoint accepts, or whether reasoning tokens eat `max_output_tokens`. Run the smoke script and record the results in `logs/04-providers-budget-schemas.md`. If the preset defaults in the architecture are wrong, fix them and tell Aditya.

## Notes per task

**04 — the security-sensitive one**
- `num_ctx` must be sent on **every** Ollama request. The named regression test for this is mandatory.
- The API key is read from the env var at call time, never logged, never in error messages, never in the cache. Grep the repo, logs, and test output for your real key before opening the PR.
- No default hosted model id anywhere in code.
- Remote endpoints must never be called without acknowledgement, including non-TTY runs.

**06 wiring**
- Order matters: consent and preflight happen **before any project file is read**. Test it with unreadable source files.
- Overwrite checks run before anything is written; a refusal leaves the tree untouched.
- Exit codes: 0 clean, 1 fatal, 2 completed with skips or `[generation failed]`.

**08 — behaving well when things go wrong**
- `--dry-run` is strictly read-only: no provider built, no prompt for acknowledgement, no `.codewhy/` created, no writes. Its file-pass numbers must match a real run; module/project numbers are labelled upper bounds.
- Atomic writes (temp file + replace); Ctrl-C exits 130 with the cache intact so the next run resumes.
- Shregna writes the failure-boundary matrix from the spec. Don't pre-empt it; if her tests expose a gap in your code, fix the code.

**09 — CI and packaging**
- The `wheel` job installs the **built wheel** (not the source tree) in a clean venv, runs `codewhy scan` against the stub Ollama server on all three OSes, and asserts a second run makes zero `/api/chat` calls. If this fails on Windows, that is a real bug to fix in the owning code with a test.
- Never publish, tag, or push release artifacts. Publishing is Aditya's action.

## Keys and environment

- Use your own free Gemini key or local Ollama. Only live checks need a key; the test suite is offline.
- Never commit a key, paste it into a prompt, work log, or screenshot, and assume anything in the agent session's environment is visible to it.
- **Never ask for or use Aditya's company key.** It is used only by him, in his own terminal, during the demo.

## Working rules

1. One branch per task, one PR, a reviewer from the table above, `main` always green.
2. Fresh Claude Code session per task, using the prompt from `tasks/ALL-TASKS.md`:

```text
Execute tasks/04-providers-budget-schemas.md.

Follow CLAUDE.md. Read docs/ARCHITECTURE.md, docs/TESTS.md, the task file, and the work
logs of earlier tasks before changing anything. Write your plan into the task's work log
first. Stay strictly inside the task's scope. Do not commit unless I say so.
When done, give me the handoff report from CLAUDE.md.
```

3. Before every PR: run `.venv/bin/python -m pytest -q` yourself, read your `git diff`, and make sure the work log's live-check lines say Passed / Failed / Not run (reason).
4. Any change to `docs/ARCHITECTURE.md` goes through Aditya, in the same PR as the code that forces it.
