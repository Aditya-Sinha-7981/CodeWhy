# Changelog

All notable changes to codewhy are recorded here. Versions follow
[Semantic Versioning](https://semver.org/); exit codes and output layout are
part of the public interface.

## [0.1.0] — unreleased

First public release.

### Added

- `codewhy scan [PATH]`: documents a local Python / JavaScript / TypeScript
  folder as one paste-ready `PROJECT_DOCS.md` (default), a mirrored
  `codewhy-docs/` tree, or both (`--output`).
  - Structural facts (definitions, imports, `Depends On` / `Used By`, entry
    point) come from parsing: Python `ast`, tree-sitter for JS/TS.
  - Explanations are written by a language model in three passes: file,
    module, project.
  - Single-file output picks render level 0, 1 or 2 to fit
    `output.token_budget` (default 20,000 estimated tokens).
- Three provider routes:
  - local **Ollama** (the default; nothing leaves the machine);
  - **Gemini** through Google's OpenAI-compatible endpoint;
  - any **OpenAI-compatible** endpoint.

  Remote endpoints require an explicit acknowledgement (`--yes`, a prompt, or
  `llm.acknowledge_remote`).
- Content-addressed SQLite cache in `.codewhy/`:
  - unchanged re-runs make zero model calls and write byte-identical files;
  - interrupted runs resume;
  - `--full` regenerates everything.
- `--dry-run`: files, cache hits and an estimate of calls and input tokens,
  with no model calls and no writes.
- `codewhy doctor` (provider preflight), `codewhy init` (writes `codewhy.toml`
  and `.toolignore`), `codewhy config show`.
- Exit codes:
  - `0`: clean;
  - `1`: fatal;
  - `2`: completed with skipped files or `[generation failed]` items;
  - `130`: interrupted.
- Output safety:
  - atomic per-file writes;
  - files codewhy did not generate are never overwritten without `--force`;
  - model text cannot create headings or code fences in the output.

### Known limitations

See the README's *Limitations* section: path aliases such as `@/…` appear as
external imports; Python syntax newer than the running interpreter is
skipped; explanations can be wrong; grounding checks (Layer 5) are not
implemented.
