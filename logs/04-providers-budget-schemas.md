# Work log — CW-04 providers, budget, schemas, consent, doctor

Branch: `task/04-providers` (from `main` at `1134e07`, CW-01–03 merged).
Implemented by Aditya's session; the task map lists Sheikh as owner, with Aditya
reviewing key handling and Shregna as second reviewer.

## 2026-10-20

### Plan (written before the first code change)

Read: `CLAUDE.md`, `docs/ARCHITECTURE.md` (Locked principles, Config, Provider
interface, Context budgeting, Privacy, Failure boundaries, Pass schemas),
`docs/TESTS.md` (Budgeting, Providers, CLI), `tasks/04-providers-budget-schemas.md`,
`members/aditya.md`, and the CW-01–03 logs. `git status`: clean on `main`.

Order of work:

1. Verify provider docs live (Ollama API, Gemini OpenAI-compat, Gemini thinking).
2. `pyproject.toml`: add `httpx`.
3. `config.py`: preset application inside `resolve_config`, provider-specific
   defaults, and the `max_output_tokens < context_tokens` check.
4. `llm/budget.py` + `tests/test_budget.py`.
5. `summarize/schemas.py` (pass models + `provider_schema`) + `tests/test_schemas.py`.
6. `llm/provider.py`: interface, errors, shared HTTP retry/backoff and error
   mapping, `build_provider`.
7. `llm/ollama_provider.py` + `tests/test_ollama_provider.py` (with the named
   `num_ctx` regression test).
8. `llm/openai_compat_provider.py` + `tests/test_openai_compat_provider.py`.
9. `consent.py` + `tests/test_consent.py`.
10. `tests/support/fake_provider.py` (`FakeProvider`, `BudgetCheckingFakeProvider`).
11. `cli.py`: `doctor`, plus `--provider`/`--model` on `scan`; `tests/test_doctor.py`.
12. `scripts/provider_smoke.py`; live checks where possible; full test run on
    3.14 and 3.11; this log; diff review.

### Provider docs verified (2026-10-20)

- **Ollama API**: https://github.com/ollama/ollama/blob/main/docs/api.md
  - `POST /api/chat` takes `model`, `messages`, `stream`, `format` ("`json` or a
    JSON schema"), and `options` (Modelfile parameters: `num_ctx`,
    `num_predict`, `seed`, `temperature`).
  - The non-streaming response carries `message.content` and `done_reason`.
  - `GET /api/tags` → `{"models": [{"name", "model", …}]}`. Names are `model:tag`,
    and the tag defaults to `latest`.
  - `GET /api/version` → `{"version": "x.y.z"}`.
- **Ollama structured outputs** (JSON schema in `format`) were introduced in
  **v0.5.0**: Ollama blog "Structured outputs", 2024-12-06
  (https://ollama.com/blog/structured-outputs), and the 0.5.0 changelog.
  Minimum version used by preflight: **0.5.0**.
- **Gemini OpenAI compatibility**: https://ai.google.dev/gemini-api/docs/openai
  (page last updated 2026-10-14).
  - Base URL `https://generativelanguage.googleapis.com/v1beta/openai/`;
    `Authorization: Bearer <key>`; `GET …/openai/models` lists models.
  - `reasoning_effort` mapping: `minimal`/`low` → `thinking_budget` 1024 on 2.5
    models, `low` thinking level on Gemini 3/4 models. `"none"` disables thinking
    on 2.5 models only; "Reasoning cannot be turned off for Gemini 2.5 Pro or 3
    models".
  - The page does not list support for `temperature`, `max_tokens`, `seed`, or
    `response_format` json_schema / json_object. Only the live check settles
    these (see Hardware checks).
- **Gemini thinking**: https://ai.google.dev/gemini-api/docs/thinking (updated
  2026-10-14): the output limit "sets the maximum number of tokens a response
  can generate, **including thought tokens**". If the model hits the limit while
  thinking, the response may be truncated or empty.

**Consequence (lead decision, 2026-10-20):** with `reasoning_effort = "low"` (a
1024-token thinking budget on 2.5 models) and the old `max_output_tokens = 1024`,
a Gemini response can be cut off or empty. The lead chose to give the gemini
preset its own default, **`max_output_tokens = 8192`** (provisional until the
live smoke check). `max_output_tokens` therefore becomes "unset = provider
default" (ollama / openai-compat 1024, gemini 8192), like `model` and
`context_tokens`. This touches CW-01 files outside the allowed list:
- `src/codewhy/templates/codewhy.toml` and `codewhy.toml.example`: the
  `max_output_tokens` line is commented out, so `init` + `provider = "gemini"`
  does not pin 1024.
- `docs/ARCHITECTURE.md`: preset and config example updated.

### Decisions (later tasks depend on these)

- **Preset application** is `config.apply_preset`, called at the end of
  `resolve_config`, so `config show`, `doctor`, and `build_provider` all see the
  same filled values. It fills only keys that are `None` (explicit user values
  win) and only for `provider = "gemini"`. `build_provider` calls it again
  (idempotent) for callers that build a `Config` directly.
- **`max_output_tokens`** is `None` when unset; `LLMConfig.effective_max_output_tokens`
  gives 1024 (ollama, openai-compat) or 8192 (gemini). An `LLMConfig` model
  validator rejects `effective_max_output_tokens >= effective_context_tokens` at
  config resolution (task §7 Req. 5). Consequence: gemini with
  `context_tokens < 8193` and no explicit `max_output_tokens` is now a config
  error with a clear message.
- **openai-compat defaults without the preset:** `structured_output` unset →
  `prompt`; `send_seed` unset → false; `api_key_env` unset → no Authorization
  header (local servers). `base_url` and `model` are required (`ConfigError` from
  `build_provider`). Recorded in the architecture.
- **`is_remote`** for openai-compat is always `True`, even for
  `http://localhost:1234`. Task §7 Req. 3 has a confusing parenthetical, but §6
  and the architecture are explicit: only ollama on loopback is local.
- **`loopback`** uses `ipaddress.is_loopback`, so all of 127.0.0.0/8 counts
  (superset of the three listed forms); brackets are stripped; `localhost` is
  case-insensitive. It lives in `consent.py` with `url_host`.
- **`ensure_remote_ack`** has one extra required keyword, `tell`, which prints
  the "Your source code will be sent to …" line. Library code may not print
  (CLAUDE.md), so the CLI passes its own printer, alongside `ask`. Outcomes:
  `ConsentRequired` (no TTY) and `ConsentDeclined` (answered no), both
  subclasses of `ConsentError`. Task 06 maps both to exit 1.
- **Retries** (`provider.send_with_retries`): transient = 429, 5xx, any
  `httpx.TransportError` (connection errors, timeouts). Delays are 1, 2, 4, …
  capped at 30s. With `Retry-After` (seconds or HTTP-date), the delay is
  `max(backoff, min(retry_after, 60))`. "Plus Retry-After" in the task is read
  as "at least Retry-After"; adding the two would double-wait. `max_transient_retries`
  counts retries, so attempts = retries + 1. `httpx.UnsupportedProtocol` (URL
  without a scheme) is not transient; it becomes a "not a valid URL" error.
- **Preflight** uses a 10s timeout (`min(timeout_seconds, 10)`) and never
  retries, so "Ollama down" fails fast. On the openai-compat listing, 429/5xx/odd
  4xx log a warning and continue; a rate limit should not block a run.
- **Gemini invalid key = HTTP 400.** Live: a fake key on `/models` and
  `/chat/completions` returns `400 {"error": {"message": "Please pass a valid API
  key", "status": "INVALID_ARGUMENT"}}`. Before this was handled, `doctor`
  passed with a bad key. A 4xx whose body contains `valid API key`,
  `API key not valid` or `API_KEY_INVALID` now maps to `AuthError`. Also observed:
  `/models` with **no** key returns 404, which would read as "no listing"; this
  is harmless because the key variable is checked first.
- **Ollama reply parsing** uses the same tolerant `extract_json_object` as the
  compat provider (harmless with native `format`). `done_reason == "length"`
  plus unparseable content → `MalformedOutput` naming `max_output_tokens`. This
  was confirmed live; the API docs do not list `"length"`.
- **Ollama 404 on `/api/chat`** → `BadRequestError` with `ollama pull <model>`;
  401/403 (a proxy) → `AuthError`.
- **json_schema request**: `{"type": "json_schema", "json_schema": {"name":
  "codewhy_result", "schema": …}}` with no `strict`, because `strict` requires every
  property in `required`, and `notes` is optional.
- **`provider_schema`** keeps only `type`, `description`, `properties`,
  `required`, `items`, `enum`. It drops length limits too: hosted endpoints
  reject some keywords, and pydantic enforces the limits afterwards anyway.
  `str | None` becomes a plain string; other unions keep their first non-null
  branch.
- **Pass models**: `notes` that is empty after stripping becomes `None`, so the
  renderer can omit it.
- **Error messages never contain the key.** Body excerpts are capped at 300
  chars, single-line, and redacted for the key value and any `Bearer <token>`.
  Logs: debug only for retry decisions (status codes), and warnings for
  version/listing problems; no prompt, response, or source text at any level.
- **`doctor`** reads only config (never `build_project`/discovery), so it opens
  no source file (tested with unreadable files).

### FakeProvider (for task 05+)

`tests/support/fake_provider.py`:
- `FakeProvider(respond=…, results=[…], errors={i: exc}, is_remote=…,
  endpoint_label=…, preflight_error=…)`.
  - Precedence per call: `errors[index]`, then the `results` queue (an item may
    be an exception, which is raised), then `respond(call)`, then
    `synthesize(schema, prompt)`.
  - `synthesize` fills required fields with deterministic text derived from
    sha256(prompt).
  - `.calls` is a list of `Call(prompt, schema, max_output_tokens, temperature)`;
    `.preflight_calls` counts preflights.
- `BudgetCheckingFakeProvider(inner, prompt_budget)` asserts
  `estimate_tokens(prompt) <= prompt_budget` on every call, then delegates.
  `.calls` proxies the inner provider's calls.

### Commands run

- `.venv/bin/python -m pip install -e ".[dev]"`: OK (httpx 0.28.1).
- `.venv/bin/python -m pytest -q`: **397 passed** (Python 3.14.7). The 147 new
  tests: budget 11, schemas 21, ollama 31, openai-compat 44, consent 26,
  doctor 14.
- Python 3.11.11 with `httpx==0.27.0` and `networkx==3.0` (the lower bounds):
  **397 passed**.
- 2026-10-21, after the HTTP 402 fix (one new openai-compat test, 45 now):
  `.venv/bin/python -m pytest -q`: **398 passed** (Python 3.14.7).
- `.venv/bin/python -m pytest -q -m live_llm`: all deselected (no live tests in
  this task; the smoke script is the live check).
- Mutation checks (each reverted):
  - drop `num_ctx` from `chat_body` → 2 failures, including the named
    regression test;
  - disable key redaction → the key-leak test fails;
  - treat 401 as transient → the not-retried test fails;
  - skip the no-TTY block → the consent test fails;
  - disable the gemini preset → 38 failures.
- `git diff --check`: clean. Repository grep for a Google key prefix (`AIza`):
  0 hits.

### Hardware / model-dependent checks

| Check | Result |
|---|---|
| `provider_smoke.py` against local Ollama with the default model `qwen2.5-coder:14b` | **Not run (model not pulled).** This machine has `qwen2.5-coder:7b`, not the 9 GB 14b; I did not pull it without asking. The script correctly reports `Run: ollama pull qwen2.5-coder:14b`. |
| `provider_smoke.py --model qwen2.5-coder:7b` (Ollama 0.34.4, macOS arm64) | **Passed** in 4.7s. Sent `options={num_ctx: 16384, num_predict: 1024, seed: 0, temperature: 0.0}` and `format=<json schema>`; `done_reason=stop`, 60 output tokens; the reply validated as `FilePass`. |
| Same with `--max-output-tokens 8` | **Passed** (expected failure path): `done_reason=length` → `MalformedOutput: … cut off at max_output_tokens (8) …`. |
| `provider_smoke.py --provider gemini --model gemini-3.6-flash`, default cap 8192 (lead's terminal, 2026-10-21) | **Failed (account, not code).** Preflight ok in 0.5s; generation got `HTTP 402 RESOURCE_EXHAUSTED "Your prepayment credits are depleted"`. Sent: `temperature=0.0, max_tokens=8192, reasoning_effort='low', response_format.type='json_schema'`. No `finish_reason`/`usage` (no completion). |
| Same with `--max-output-tokens 1024` | **Failed (same HTTP 402).** Sent `max_tokens=1024`, otherwise identical. The reasoning-vs-cap question is still open. |
| `doctor`, Ollama down | **Passed**: `error: Cannot reach Ollama at http://localhost:11434. Start it with \`ollama serve\` (or install from ollama.com).`, exit 1. |
| `doctor`, model not pulled (default 14b) | **Passed**: `error: Model 'qwen2.5-coder:14b' is not available locally. Run: ollama pull qwen2.5-coder:14b`, exit 1. |
| `doctor --model qwen2.5-coder:7b` (healthy) | **Passed**: full report, exit 0. |
| `doctor` Gemini with a bad key (a fake string, not a real key) | **Passed after a fix**: initially exit 0 (the HTTP 400 was taken for "no listing"); now `error: … rejected the key in GEMINI_API_KEY (HTTP 400) …`, exit 1. |
| `doctor --provider gemini --model gemini-3.6-flash` with the lead's real key (lead's terminal, 2026-10-21) | **Passed**: preflight ok, exit 0; `endpoint: generativelanguage.googleapis.com (remote)`, `max_output_tokens: 8192`, `prompt budget: ~22118 tokens`. The key is valid and `/models` works. Note: `doctor` cannot detect a billing block, because listing models is free. |

**Gemini parameter support (task §7 Req. 3)**: Still open. The 2026-10-21 runs
were blocked by HTTP 402 (credits depleted) before any parameter was judged, so a
402 says nothing about which parameters are accepted. Re-run after topping up
credits or with a free-tier key. **Defect found and fixed:** the 402 was reported
as "Check llm.model and the optional parameters", which sends the user to the
wrong place. HTTP 402 now gets its own message pointing at billing, is not
retried, and has a regression test
(`test_402_points_at_billing_not_config_and_is_not_retried`). To fill it in, run this
in your own terminal (not an agent session):

```bash
export GEMINI_API_KEY=...            # your free AI Studio key
.venv/bin/python -m codewhy doctor . --provider gemini --model <id>
.venv/bin/python scripts/provider_smoke.py --provider gemini --model <id>                          # cap 8192
.venv/bin/python scripts/provider_smoke.py --provider gemini --model <id> --max-output-tokens 1024 # reasoning vs cap
unset GEMINI_API_KEY
```

Record for each run: PASS/FAIL, the `sent:` line (which parameters were
accepted), `finish_reason`, and `usage` (`completion_tokens`, and any reasoning
token count). If the default-cap run fails with a 400 naming a parameter, that
parameter is unsupported: note it, and set `send_seed`/`reasoning_effort`/
`structured_output` accordingly in the preset. Rows to fill:
temperature / max_tokens / response_format json_schema / reasoning_effort /
seed (send it via `send_seed = true` in a scratch `codewhy.toml`).

### Manual verification (task §8)

1. `doctor` with Ollama stopped: **Passed** (message says `ollama serve`).
2. `codewhy config show --provider gemini --model x`: **Passed**. All preset values are visible
   (`base_url`, `api_key_env = "GEMINI_API_KEY"`, `structured_output =
   "schema"`, `reasoning_effort = "low"`, `send_seed = false`,
   `context_tokens = 32768`, `max_output_tokens = 8192`); no key appears.
3. Grep for the real API key: **Not run** as stated (no real key was used in this
   session). The proxy check, a grep for the `AIza` prefix across the repo,
   found 0 hits. Re-run `grep -rn "<your key>" .` after the Gemini smoke run.

### Deviations from the task / files outside the allowed list

- `src/codewhy/templates/codewhy.toml`, `codewhy.toml.example` (CW-01): the
  `max_output_tokens` line is commented out, with provider defaults explained
  (lead decision above). Still byte-identical to each other.
- `tests/test_config.py` (CW-01): three assertions now use
  `effective_max_output_tokens`. A gemini test used `context_tokens = 8000`,
  which the new validation rejects; it now uses 20000. The same case is now
  tested as an error in `test_budget.py`.
- `docs/ARCHITECTURE.md`: gemini preset `max_output_tokens = 8192` and why;
  provider default for `max_output_tokens`; the cap-below-context rule;
  openai-compat defaults without the preset; Ollama preflight (min 0.5.0);
  invalid-key-as-400; backoff numbers. This is a contract change, approved by
  the lead in this session.
- `FakeProvider` tests live in `tests/test_schemas.py`; the allowed list has no
  file of their own.

### Open issues / for the project lead

- **Gemini live check (above) is still open.** It is required before merge
  under lead rule 1 ("live-check entries are filled in"), but on 2026-10-21 the
  lead waived it for this commit after the runs were blocked by HTTP 402
  (billing). It still has to settle the parameter table and whether 8192 is
  right; re-run the smoke commands once the account has credits.
- `structured_output = "json"` against OpenAI proper requires the word "JSON" in
  the prompt; `prompt` mode relies entirely on the prompt describing the schema.
  Both are task 05's prompt responsibility. The provider does not inject
  schema text.
- `reasoning_effort` is a free string (config has no enum); a typo reaches the
  endpoint as a 400 with a clear message. Not tightened (not in scope).

### Handoff notes

- Task 05:
  - Use `Provider` (`codewhy.llm.provider`), `FakeProvider` and
    `BudgetCheckingFakeProvider` (`tests.support.fake_provider`),
    `prompt_budget`/`estimate_tokens` (`codewhy.llm.budget`), and `FilePass`,
    `ModulePass`, `ProjectPass` and `provider_schema` (`codewhy.summarize.schemas`).
  - Budget with `llm.effective_context_tokens` and
    `llm.effective_max_output_tokens`, never the raw fields (they may be
    `None`).
  - `MalformedOutput.raw` holds up to 2000 chars of the reply for the
    retry-with-error prompt.
- Task 06:
  - Wire `build_provider(config)`, then
    `ensure_remote_ack(provider, assume_yes=--yes,
    acknowledged_in_config=llm.acknowledge_remote, is_tty=sys.stdin.isatty(),
    ask=typer.confirm-like, tell=typer.echo)`, then `provider.preflight()`, all
    before `build_project`.
  - Map `ConsentError`/`ProviderError`/`ConfigError` to exit 1; call
    `provider.close()` when done.
- Task 07: hash `reasoning_effort`, `structured_output`,
  `effective_max_output_tokens` and `effective_context_tokens` into `config_fp`.
