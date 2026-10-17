# AGENTS.md — codewhy

Read this file and `CLAUDE.md` before changing code or documentation. This is
the shared operating guide for the `codewhy` repository.

## Project and source of truth

`codewhy` is a Python CLI that scans a local Python and/or JavaScript/TypeScript
folder and generates Markdown explaining what each file does, why it exists,
and how the project fits together. Deterministic parsing supplies facts; an LLM
supplies narrative. The default output is a paste-ready `PROJECT_DOCS.md`.

Authority order:

1. `docs/ARCHITECTURE.md` — behavior and contracts.
2. `docs/TESTS.md` — the acceptance test plan.
3. The active `tasks/NN-*.md` work order — permitted scope and deliverables.
4. `docs/PROJECT_SPEC.md` — background rationale only.
5. `CLAUDE.md` — detailed standing implementation rules.

Do not treat architecture or task documents as evidence that a feature works:
inspect the implementation and run the relevant checks.

## Team and ownership

There are three project members. Coordinate at the task boundary and preserve
the contracts each stream exposes.

| Member | Primary ownership |
| --- | --- |
| Aditya | Task 01 scaffold/config/discovery; task 05 summarization/pipeline; task 07 cache; task 09 release; architecture approval and integration. |
| Sheikh | Repository setup; task 04 providers; task 06 scan wiring; task 08 CLI robustness; task 09 CI, packaging, stub server, and README commands. |
| Shregna | Tasks 02–03 extraction, resolution, graph, and entry point; task 06 rendering; task 08 failure matrix; task 09 evaluation. |

The detailed handoffs, prerequisites, reviewers, and branch names are in
`members/aditya.md`, `members/sheikh.md`, and `members/shregna.md`. Read the
relevant member plan before starting shared or dependent work.

## Before and during work

1. Read `CLAUDE.md`, the authoritative docs above, the entire active task, and
   any existing `logs/<task>.md`.
2. Check Git state and inspect the files you will touch. Do not overwrite or
   discard another member's changes.
3. Record a short plan in the task work log before substantial implementation.
4. Make the smallest change that fulfils the work order. Keep deterministic
   core logic separate from I/O and preserve cross-platform behavior.
5. Update the architecture in the same change only when a required contract
   changes; obtain Aditya's approval for such a change.
6. Run the task's focused automated checks, record manual/live checks honestly,
   review the diff, and provide the handoff required by `CLAUDE.md`.

Never expand a task's scope silently. Stop for direction if a material contract
is missing or conflicts with the requested work.

## Non-negotiable project rules

- Python 3.11+ with a `src/` layout. Public functions have type hints.
- Project paths are project-relative POSIX strings; normalize CRLF to LF at the
  filesystem boundary. Sort iterated data so identical input yields identical
  output.
- Deterministic parsers establish facts. LLM calls occur only through provider
  code, and tests outside live end-to-end checks stay offline.
- Never log or commit source code, prompts, model output, API keys, or secrets.
  Read hosted-provider keys at call time only. Do not use another member's key.
- `.gitignore` is intentionally not an input to codewhy discovery. `.toolignore`
  is the user-facing discovery control. The generated `.codewhy/` cache is
  ignored and must remain free of source text and secrets.
- Do not commit, push, open a pull request, merge, publish, or tag unless the
  user explicitly authorizes that specific action.

## Repository hygiene

`local/` is intentionally ignored: it may contain personal planning and local
reference material, and it is not an authoritative project input. Keep tracked
work logs in `logs/`; do not put secrets or private source material in them.

This repository is being initialized before task 01. Do not create application
scaffolding, dependencies, or CI outside the active work order.
