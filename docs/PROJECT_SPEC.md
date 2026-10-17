> **Status: background rationale only.** `ARCHITECTURE.md` is the source of truth for
> implementation. Where this document disagrees with it (build phases, caching design,
> output behavior, provider support, Layer 5 scope), `ARCHITECTURE.md` wins. The
> project name is locked: **codewhy**.

# Project Spec: Local Codebase Documentation Generator

Name: **codewhy**

---

## 1. What this is

A CLI tool that you point at a **local folder** (no Git, no GitHub, no repo required) and it generates a set of Markdown files explaining:
- what each file is and what it does
- why it exists (its role in the project, not just its symbols)
- how the pieces connect to each other

Output is plain `.md` files, sitting next to the code, that a human can read to understand a project cold, or that you paste into any AI chat (free tier, no agentic tools required) to get instant project context.

## 2. Why this, specifically

Recap of the gap-hunting we did, so this doc stands on its own:

- **DeepWiki** (Cognition) and its open clone auto-generate wiki-style docs for a codebase — but only for repos hosted on GitHub/GitLab/Bitbucket. You give it a URL; it clones from there. A folder that's never been `git init`-ed doesn't exist to it.
- **skeletree**, **Aider's repo-map**, and similar tree-sitter-based tools *do* work on local folders — but they deliberately stop at structure. Function signatures, class names, import graphs. No explanation of what anything does or why. That's a design choice on their part (keeps it fast, deterministic, cheap) — not an oversight we get to "beat," just a different job than ours.
- Nothing currently combines: **local-first (no git needed) + narrative explanation (not just structure)**, aimed at someone who just has a folder of code and wants to understand or share it.

That's the niche. It's not virgin territory in the broad sense (code-understanding tools are a crowded space) but this specific combination is open.

## 3. Target user & scope

- Someone with a **local, possibly ungitted folder** of a few to a few dozen files.
- Concretely: a student whose "backtracking assignment" or side project is split across `main.py`, `solver.py`, `utils.py`, whatever — and either wants to *understand it themselves* after coming back to it later, or wants to *hand it to an AI chat* without re-explaining the whole structure every time.
- **Languages for v1: Python and JavaScript/TypeScript only.** Not because the tool can't theoretically support more, but because scoping to two lets us do real deterministic parsing (Python's `ast`, tree-sitter for JS/TS) instead of guessing structure with an LLM.

### Explicit non-goals (say these out loud in your report — it shows judgment, not limitation)
- Not a hosted wiki / website. Output is files, not a service.
- Not an editor plugin or IDE integration for v1.
- Not a code-editing agent (that's Aider's job, not ours).
- Not attempting more than 2 languages for v1.
- Not doing computed data analysis or running the code — purely reading and explaining it.

## 4. Architecture

Five layers, roughly in the order data flows through them:

### Layer 1 — Extraction (deterministic, no LLM)
- Python files parsed with the stdlib `ast` module.
- JS/TS files parsed with `tree-sitter` (prebuilt grammars, no compiler needed on the user's machine).
- Output per file: list of functions/classes/methods with signatures, imports, existing docstrings/comments, rough line count.
- This step never calls an LLM. It's 100% deterministic — same input, same output, every time, free.

### Layer 2 — Dependency graph
- Resolve imports across files (relative imports, local modules — not third-party packages) into a graph using `networkx`.
- This tells us which files reference which, so later summarization isn't done on files in isolation.
- v1 can keep this simple (who-imports-whom). Ranking by importance (PageRank-style, like Aider does) is a v2 nice-to-have, not a launch requirement.

### Layer 3 — State / caching
- Content-hash (or mtime) each file.
- SQLite (stdlib `sqlite3`) stores: file path → hash → last-generated summary → timestamp.
- On re-run, only re-summarize files whose hash changed, and propagate "this module needs updating" upward to any module/project-level doc that depended on it.
- This is what makes it usable as a real tool instead of a one-shot script — rerun after every coding session without re-paying for the whole project every time.

### Layer 4 — Summarization (the LLM layer, hierarchical, map-reduce style)
1. **File-level pass**: for each file, prompt the model with its extracted structure (Layer 1 output) plus, for small files, the actual source. Ask for a plain-language explanation of what the file does and why it exists.
2. **Module-level pass**: group files by folder (or by graph clustering later), feed the model the file-level summaries + the dependency edges between them (Layer 2), ask for a summary of what the module/folder is responsible for and how its pieces cooperate.
3. **Project-level pass**: feed all module summaries, ask for a top-level overview — what the project is, its main entry point, how data/control flows through it.
- This is the piece nobody else's tool does — structure-only tools stop at Layer 1/2, we go on to actually explain it.

### Layer 5 — Consistency & grounding
- **Consistency**: carry forward a running glossary of terms/names as context into later prompts, or run one reconciliation pass at the end, so file A and file B don't describe the same class two different ways.
- **Grounding**: flag low-confidence explanations rather than stating everything with false certainty. Optionally let the model re-read a function body when its own confidence is low, instead of only trusting the signature.

### Output
- A `docs/` folder mirroring the project structure: one `.md` per source file, one per module/folder, one `PROJECT_OVERVIEW.md` at the root.
- Config option to instead emit a single consolidated file for very small projects (matches the "backtracking assignment in 3 files" use case — no one wants 3 separate doc files for that).

## 5. Tech stack (and why)

| Piece | Choice | Why |
|---|---|---|
| Core language | Python | Stdlib `ast` for Python parsing, strong tree-sitter bindings, best ecosystem for the orchestration/graph/cache work which is where the real difficulty lives |
| Python parsing | `ast` (stdlib) | Zero dependencies, richest output for the language we care most about |
| JS/TS parsing | `tree-sitter` + prebuilt grammar wheels | No compiler required on the user's machine — ships precompiled |
| Dependency graph | `networkx` | Standard, well-documented, does exactly what we need without writing graph algorithms by hand |
| Cache/state | `sqlite3` (stdlib) | Zero setup, transactional, good enough for this scale |
| LLM calls | Pluggable provider interface — Ollama (local) as default, Gemini free tier or others as a swap-in | Keeps it free/private by default, matches your original "start local, scale later" instinct |
| CLI | `typer` (or `click`) | Clean decorator-based CLI, minimal boilerplate |
| Packaging | `pyproject.toml`, distributed via `uvx`/`pipx` entry point | User runs one command, zero manual dependency wrangling — this is what solves your "not gonna make someone install 100 libs" concern |

## 6. Build phases

Roughly matching a semester timeline, each phase individually demoable:

1. **Extraction + graph** — parse Python & JS/TS files, build the import graph. No LLM yet. Verify on a few real local projects (including your own messier ones).
2. **MVP narration** — single-pass file-level summaries only, no caching, no hierarchy. Ugly but end-to-end and demoable early.
3. **Hierarchical rollup + basic caching** — file → module → project summaries, mtime-based re-run support.
4. **Dependency-aware summarization + consistency pass** — feed graph edges into prompts, reconcile terminology across files. This is the phase that actually separates the tool from a prompt wrapper.
5. **Grounding + polish + packaging** — confidence flagging, CLI UX, README, `uvx`-installable package, demo repo for your submission.

## 7. How we work from here

Same process you used for SIH: lock architecture (this doc) → lock exact library/model choices if anything above still needs deciding → build phase by phase with Claude Code implementing against this spec, so every session's work stays consistent with what's decided here instead of drifting. Update this file as decisions change; don't let the build get ahead of the spec.

## 8. Naming ideas (pick one, or none of these and move on)
- `localdocs`
- `codewhy` (leans into "why this file exists," which is your actual differentiator)
- `folderwiki`
- `explainlocal`
