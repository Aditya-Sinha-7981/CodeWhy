# codewhy — Aditya's plan (lead)

You own the architecture, the critical path (01 → 05 → 07), the release, and the live demo. You also review everyone else's work. Read `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/TESTS.md`, and `tasks/ALL-TASKS.md` first; the team split is the table below.

## Team map (who owns what)

| Task | Owner | Reviewer(s) |
|---|---|---|
| 01 scaffold, config, discovery | **Aditya** | Sheikh |
| 02 extraction | Shregna | Aditya |
| 03 resolution, graph, entry point | Shregna | Aditya |
| 04 providers, budget, consent, `doctor` | Sheikh | Aditya (key handling) + Shregna |
| 05 summarization passes | **Aditya** | Shregna + Sheikh (both) |
| 06 render builders (Req 1–4, 6) | Shregna | Aditya |
| 06 `scan` wiring (Req 5) + integration tests | Sheikh | Shregna |
| 07 cache | **Aditya** | Sheikh |
| 08 CLI robustness (except the failure matrix) | Sheikh | Aditya |
| 08 failure-boundary matrix test (Req 4) | Shregna | Aditya |
| 09 release, README claims check, demo | **Aditya** | — |
| 09 CI, packaging, stub server, README commands | Sheikh | Aditya |
| 09 evaluation runner and results | Shregna | Aditya |

## Your tasks, in order

| # | Starts when | Branch | You deliver | Others waiting on you |
|---|---|---|---|---|
| 01 | Now, alone | `task/01-scaffold` | Installable package, config, discovery, `init`, `config show` | **Everyone** (merge this first, quickly) |
| 05 | 02, 03 and 04 are merged | `task/05-passes` | Prompts, three passes, chunking, ladders, orchestrator | Shregna (render), Sheikh (wiring) |
| 07 | 05 is merged (`db.py`/`keys.py` can start immediately; integration waits for 06) | `task/07-cache` | Cache, resume, byte-identical re-runs | Sheikh (dry-run needs `peek`) |
| 09 | 08 is merged | `task/09-release` | Release steps, README accuracy pass, demo | — |

**Task 05 contracts commit.** Within your first hours on 05, push a small commit with only `summarize/result.py` (`PassResult`) and the `RunResult` dataclass in `pipeline.py`, tests-free. Shregna's render work depends on these types and can start in parallel as soon as they exist.

## While you are not coding

- **Stage 0:** after merging 01, write eval questions with **ground-truth answers** for your own project(s) (the agent must not invent these; task 09 depends on them). Decide who owns the PyPI account (should be you) and reserve `codewhy` with a placeholder release.
- **Stages 1–2 (before 05): the prompt lab.** In a throwaway script (not committed), take 2–3 real files from your own projects and try prompt wordings against a real model (Ollama and a free Gemini key) until the Purpose output explains *why a file exists*, not what its functions are. Write the winning wording and the failures into `logs/05-summarization-passes.md`. This is the single biggest quality lever in the project.
- **Always:** review PRs within a day. Reviews check scope (no creep), that tests really cover the task's list, and that no contract in `docs/ARCHITECTURE.md` changed silently.

## Lead rules (you enforce these)

1. One branch per task, one PR, merged only when the task's tests pass on the CI matrix and its live-check entries are filled in (Passed / Failed / Not run with reason).
2. `main` is always green. If a merge breaks it, revert first, fix second.
3. Any change to `docs/ARCHITECTURE.md` needs your approval and must be in the same PR as the code that forces it.
4. Fresh Claude Code session per task, using the prompt in `tasks/ALL-TASKS.md`. Work logs are committed.
5. Nobody starts task N+1 on top of failing tests from task N.

## Keys

- **Your free key** for development and live checks (Google AI Studio), set only in your own terminal.
- **The company key never goes into any Claude Code session.** An agent can read environment variables of its session, so the company key is used only in your own terminal during the demo.
- Never commit a key; never paste one into chat, a work log, a screenshot, or a screen recording. If one leaks, revoke it immediately.

## Demo plan with the company key (1–2 runs)

Before the day:

1. **Get permission** to use the company key for a college demo, and use a **project you own** as the demo target (not company code). Ask for a separate, revocable demo key if possible.
2. **Warm the cache** on the demo project with a free key, using the **same provider, model, and settings** you will use in the demo (the cache key does not include the API key, so a different key still hits the cache).
3. **Rehearse** the whole demo end to end with the free key, including the edit-one-file step.
4. Keep a **fallback**: a pre-generated `PROJECT_DOCS.md` in `examples/`, in case the network or API fails. Preflight runs before the cache, so a flaky venue connection can break even a fully cached hosted run.
5. Run `codewhy scan <path> --dry-run` first; it shows calls and estimated tokens before you spend company credit.

On the day, in your own terminal (not an agent session, not a shared screen showing your shell history):

```text
export GEMINI_API_KEY=<company key>       # paste once, out of camera view
codewhy scan <demo-project> --provider gemini --model <id> --dry-run
codewhy scan <demo-project> --provider gemini --model <id>      # mostly cache hits (say so out loud)
# edit one file, then:
codewhy scan <demo-project> --provider gemini --model <id>      # only the affected passes re-run
unset GEMINI_API_KEY
```

Afterwards: revoke the demo key if it was separate; otherwise confirm with the company that nothing needs rotating.

## Your checklist per task

- Read the task file fully, plan in the work log first, then implement.
- Run `.venv/bin/python -m pytest -q` yourself before every PR; don't trust the agent's report.
- Do the task's manual verification yourself (task 05: read real model output; task 06's paste test is yours too, even though Sheikh and Shregna build it).
