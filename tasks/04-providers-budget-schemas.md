# codewhy Task Specification

> **This file is a work order for the assigned developer and their AI coding agent.**
>
> This task defines the scope of the assigned work. Do not expand the scope without explicit approval from the project lead.

---

## Task Information

**Task:** `LLM foundation: providers (ollama, openai-compat, gemini preset), budget, schemas, consent, doctor`

**Task ID:** `CW-04` (index: `tasks/ALL-TASKS.md`)

**Depends on:** CW-01 (config). Independent of CW-02/03 in code; run after them for ordering.

**Branch:** `task/04-providers` (suggested; create only when the user wants a branch)

**Priority:** `Critical`

**Status:** `Not Started`

**Work log:** `logs/04-providers-budget-schemas.md`

---

# 1. Goal

## Objective

Build everything the summarization passes need to talk to a model safely, without writing any pass logic. Deliverables:

1. **`Provider` interface** and error types.
2. **`OllamaProvider`** on Ollama's native API (explicit `num_ctx` on every request, native JSON-schema output).
3. **`OpenAICompatProvider`** with the **`gemini` preset** (Google's OpenAI-compatible endpoint).
4. **`llm/budget.py`**: token estimation and the prompt budget.
5. **`summarize/schemas.py`**: pydantic pass schemas plus the provider-facing schema simplifier.
6. **Remote-endpoint consent logic** and loopback detection.
7. **`FakeProvider`** (test double) used by every later task.
8. **`codewhy doctor`** and `--provider`/`--model` overrides.
9. A **live smoke script** the user runs once with a real Ollama and/or Gemini key.

## Why This Exists

The two highest-risk failures in this product live here: a silently truncated prompt (Ollama's default context is small and applies if `num_ctx` is omitted) and malformed structured output from a model. Both must be made impossible, not unlikely. And the "anyone can run it" goal depends on a hosted option working with just a key and a model id, and on code never leaving the machine without an explicit yes.

---

# 2. Authoritative Documentation

* `CLAUDE.md`
* `docs/ARCHITECTURE.md` — *Locked principles*, *Config* (`[llm]`, the `gemini` preset, `context_tokens`), *Provider interface*, *Context budgeting*, *Privacy and remote endpoints*, *Failure boundaries*, *Pass schemas*
* `docs/TESTS.md` — *Budgeting and prompt assembly*, *Providers*, *CLI* (`doctor`, remote acknowledgement)
* Current provider documentation, **verified live while building** (record URLs and dates in the work log):
  * Ollama API: `/api/chat` (`format` JSON schema, `options.num_ctx`, `num_predict`, `seed`, `temperature`), `/api/tags`, `/api/version`.
  * Google Gemini API OpenAI-compatibility page (base URL, auth, supported parameters, structured outputs, `reasoning_effort`).

If provider docs and this task disagree, trust the docs, log the difference, and update `docs/ARCHITECTURE.md` where the contract is affected.

---

# 3. Scope

## In Scope

* `src/codewhy/llm/{__init__,provider,ollama_provider,openai_compat_provider,budget}.py`, `src/codewhy/summarize/{__init__,schemas}.py`, preset application in `config.py`, `consent.py` (or equivalent module) for the remote-endpoint check.
* Runtime dep: `httpx`.
* `tests/support/fake_provider.py`, `scripts/provider_smoke.py`.
* `codewhy doctor [PATH]`; `--provider`/`--model` on `doctor` and `scan` (scan's pipeline still a stub).
* Tests in §8.

## Out of Scope

* Prompts, passes, retries-on-malformed-output, chunking (task 05). The provider raises `MalformedOutput`; it does not retry it.
* Rendering, cache, progress output, `--dry-run`.
* Additional provider kinds beyond ollama / openai-compat / gemini preset.
* Secret redaction, streaming, async, concurrency.
* Any Anthropic/OpenAI-specific features beyond what the generic compat client needs.

Do not implement "while we're here" improvements outside this scope.

---

# 4. Allowed Files / Areas

```text
pyproject.toml                                (add httpx only)
src/codewhy/config.py                         (preset application, provider-name validation)
src/codewhy/cli.py                            (doctor; --provider/--model)
src/codewhy/consent.py
src/codewhy/llm/__init__.py
src/codewhy/llm/provider.py
src/codewhy/llm/ollama_provider.py
src/codewhy/llm/openai_compat_provider.py
src/codewhy/llm/budget.py
src/codewhy/summarize/__init__.py
src/codewhy/summarize/schemas.py
tests/support/__init__.py
tests/support/fake_provider.py
scripts/provider_smoke.py
tests/test_budget.py
tests/test_schemas.py
tests/test_ollama_provider.py
tests/test_openai_compat_provider.py
tests/test_consent.py
tests/test_doctor.py
logs/04-providers-budget-schemas.md
```

### May Modify If Required

```text
docs/ARCHITECTURE.md   (provider facts that live verification proves wrong — logged)
docs/TESTS.md
```

---

# 5. Dependencies

| Dependency | Type | Notes |
|---|---|---|
| CW-01 config model | Hard | `llm` keys already exist; this task applies presets. |
| `httpx` | Runtime | Locked in the architecture. |
| Ollama installed with the default model pulled | Environment | Only for the live smoke check; tests are offline. |
| A Gemini API key (`GEMINI_API_KEY`) and a current model id | Environment | Only for the live smoke check. The key must never be committed, logged, or pasted into the work log. |

### Dependency Status

* [x] Dependency exists and is ready
* [ ] Dependency is being developed separately
* [x] Dependency requires coordination (live Ollama/Gemini for the smoke check)
* [ ] No external dependency

---

# 6. Contracts That Must Be Preserved

### Architecture

* `ollama` uses the **native** API and sends `options.num_ctx = context_tokens` on **every** request. A code path that builds a request without it is a defect.
* Structured output: Ollama `format` = JSON schema; openai-compat modes `schema` (`response_format` json_schema), `json` (`response_format` json_object), `prompt` (neither). **Every response is parsed and left for the pass layer to validate**; the provider itself returns a parsed `dict` or raises.
* Preset `gemini`: base URL `https://generativelanguage.googleapis.com/v1beta/openai/`, `api_key_env = GEMINI_API_KEY`, `structured_output = schema`, `reasoning_effort = low`, `send_seed = false`, default `context_tokens = 32768`; any key user-overridable; `model` required, **no default model id anywhere in the code**.
* openai-compat always sends `temperature` and `max_tokens`; `seed` only if `send_seed`; `reasoning_effort` only if set.
* Prompt budget: `estimate_tokens(text) = ceil(len(text) / 3)`; `prompt_budget = floor((context_tokens - max_output_tokens) * 0.9)`.
* Remote = anything other than an `ollama` provider whose host is loopback (`localhost`, `127.0.0.1`, `::1`). Remote requires acknowledgement: interactive `y/N`, `--yes`, or `llm.acknowledge_remote = true`. No TTY and no acknowledgement → exit 1 with the message, **zero** provider calls.
* The API key is read only from the env var named in config, at call time; never logged, stored in the cache, written to any file, or included in error messages.
* Errors: 401/403 → `AuthError` (never retried); 429/5xx/timeout/connection errors → transient, retried inside the provider with exponential backoff up to `max_transient_retries` and honoring `Retry-After`; other 4xx → `BadRequestError` with a key-redacted body excerpt; unparseable JSON → `MalformedOutput`.

### Safety

* No code, prompt, or response text may appear in logs at any level above debug, and never the API key at any level.
* Network access exists only inside provider classes and `doctor`'s preflight.

---

# 7. Implementation Requirements

### Requirement 1 — Interface and errors (`llm/provider.py`)

```python
class Provider(Protocol):
    is_remote: bool
    endpoint_label: str
    def preflight(self) -> None: ...
    def generate_json(self, prompt: str, schema: dict, *, max_output_tokens: int,
                      temperature: float | None = None) -> dict: ...

class ProviderError(Exception): ...                       # message is user-presentable and says how to fix it
class AuthError(ProviderError): ...
class TransientError(ProviderError): ...                  # raised only after retries are exhausted
class BadRequestError(ProviderError): ...
class MalformedOutput(ProviderError):                     # carries the raw text (truncated to 2000 chars)
    raw: str
```

`temperature=None` means "use the configured value". `build_provider(config: Config, *, sleep=time.sleep, client=None) -> Provider` constructs the right provider after preset application; `sleep` and `client` are injectable for tests. Unknown provider → `ConfigError`. Missing `model` for non-ollama → `ConfigError` ("llm.model is required for provider 'gemini'; model ids change, so codewhy does not ship a default").

### Requirement 2 — `OllamaProvider`

* `POST {host}/api/chat`, body: `model`, `messages=[{"role":"user","content":prompt}]`, `stream=false`, `format=<provider-facing schema>`, `options={num_ctx, temperature, seed, num_predict=max_output_tokens}`. Parse `message.content` as JSON → dict. Non-JSON → `MalformedOutput`.
* `is_remote = not loopback(host)`; `endpoint_label = host`.
* **Preflight:** `GET /api/version` (verify and record the minimum Ollama version that supports `format` with a JSON schema; fail with that requirement in the message if older); `GET /api/tags`: connection refused/timeout → "Cannot reach Ollama at <host>. Start it with `ollama serve` (or install from ollama.com)."; model absent → "Model '<model>' is not available locally. Run: ollama pull <model>". A bare model name without a tag matches `<name>:latest`.
* Transient handling per the contract above; `timeout_seconds` applies per request.

### Requirement 3 — `OpenAICompatProvider` and the `gemini` preset

* `POST {base_url}/chat/completions` with `Authorization: Bearer <key>` (key read at call time from `os.environ[api_key_env]`; missing → `AuthError` naming the **variable**, never a value). Body per the contract. Parse `choices[0].message.content` as JSON; tolerate Markdown code fences and leading/trailing prose by extracting the first balanced JSON object; failure → `MalformedOutput`. Empty `choices` or `finish_reason == "length"` with unparseable content → `MalformedOutput` whose message mentions truncation and `max_output_tokens`/reasoning.
* `is_remote = True` always (even for a local LM Studio URL — `is_remote` is derived from the URL: loopback hosts are local); `endpoint_label` = the URL's host.
* **Preflight:** env var set; `GET {base_url}/models` per the architecture (401/403 → auth message; 404/405/501 → skip; connection/timeout → unreachable message; model missing from a successful listing → warning only). Never print the key.
* **Gemini live verification (record results in the work log):** which of `temperature`, `max_tokens`, `response_format` json_schema, `reasoning_effort`, and `seed` the endpoint accepts today; whether `max_tokens` is honored; and — critically — whether reasoning tokens count against the output cap, i.e. whether a 1024-token cap can truncate or empty the JSON when `reasoning_effort` is `low`. If the architecture's preset defaults are wrong, fix the preset and `docs/ARCHITECTURE.md`, and tell the project lead.
* `structured_output = "schema"` sends the simplified schema; if the endpoint rejects it with a 400, raise `BadRequestError` whose message suggests `structured_output = "json"` or `"prompt"` — do **not** auto-fallback silently.

### Requirement 4 — Provider-facing schema helper and pass schemas (`summarize/schemas.py`)

* Pydantic v2 models (`extra="forbid"`, strings stripped, required strings non-empty after strip, generous max lengths to catch runaway output):
  * `FilePass(purpose: str, notes: str | None = None)` — `purpose` max 1200 chars, `notes` max 1200.
  * `ModulePass(responsibility: str, how_pieces_cooperate: str)` — max 1500 each.
  * `ProjectPass(overview: str, architecture: str)` — max 2500 each.
* `provider_schema(model: type[BaseModel]) -> dict`: inlines any `$defs`, replaces `anyOf: [{type: string}, {type: null}]` with a plain `{"type": "string"}`, drops `title`/`default`, keeps `type: object`, `properties` (with descriptions), and `required` (optional fields are not required). The result must contain no `$ref`, `$defs`, or `anyOf`.
* Field `description`s on the models are instructions to the model (what a good value contains); keep them short and concrete. Task 05's prompts will rely on them.

### Requirement 5 — Budget (`llm/budget.py`)

`estimate_tokens(text) -> int`, `prompt_budget(context_tokens, max_output_tokens) -> int`, `fits(text, budget) -> bool`. Pure functions, no I/O. Reject nonsense (`max_output_tokens >= context_tokens`) with `ConfigError` at config-resolution time.

### Requirement 6 — Consent (`consent.py`)

`ensure_remote_ack(provider, *, assume_yes: bool, acknowledged_in_config: bool, is_tty: bool, ask: Callable[[str], bool]) -> None`: local → return; remote with `assume_yes` or config acknowledgement → return; remote + TTY → print "Your source code will be sent to <endpoint_label>." and call `ask("Continue? [y/N]")`, abort (exit 1) on no; remote + no TTY → raise `ConsentRequired` whose message says to pass `--yes` or set `llm.acknowledge_remote = true`. `loopback(host)` helper handles `localhost`, `127.0.0.1`, `::1`, and bracketed IPv6 forms.

### Requirement 7 — `FakeProvider` (`tests/support/fake_provider.py`)

Implements `Provider`. Records every call as `Call(prompt, schema, max_output_tokens, temperature)`. Scriptable: a callable `respond(call) -> dict` or a queue of results; can raise `MalformedOutput`, `TransientError`, `AuthError` on chosen calls; `is_remote`/`endpoint_label` settable; default response synthesizes a schema-valid dict from the schema (fills required strings with deterministic text derived from a hash of the prompt). A `BudgetCheckingFakeProvider` wrapper asserts `estimate_tokens(prompt) <= prompt_budget` on every call (used by task 05's invariant test).

### Requirement 8 — CLI

* `codewhy doctor [PATH] [--provider P] [--model M]`: resolve config (with overrides, presets applied), build the provider, run `preflight`, print a short report (provider, model, endpoint, local/remote, `context_tokens`, prompt budget); exit 0 on success; on `ProviderError`/`ConfigError` print the message to stderr and exit 1. Makes no writes and does **not** send source code (preflight only).
* `scan` and `config show` accept `--provider` and `--model`; `config show` prints the **resolved** config including preset-filled values.
* `--provider` with an unknown value → exit 1 listing valid values.

### Requirement 9 — Live smoke script (`scripts/provider_smoke.py`)

`python scripts/provider_smoke.py [--provider P] [--model M] [--path PATH]`: builds the provider from config, runs `preflight`, then one real `generate_json` call with the `FilePass` schema and a small fixed prompt, validates with pydantic, prints PASS/FAIL, timing, and which parameters were sent. Never prints the key or the prompt body beyond a short label.

### Error Handling

* Every `ProviderError` message says what happened and exactly how to fix it; no tracebacks reach the user for expected failures.
* Backoff: delays 1s, 2s, 4s… capped at 30s, plus `Retry-After` when present (cap at 60s); `max_transient_retries` total retries; the injected `sleep` makes tests instant.

---

# 8. Testing Requirements

## Automated Tests

(All offline; HTTP is mocked via `httpx.MockTransport` or equivalent.)

* `test_budget.py`: `estimate_tokens` deterministic and monotonic; `prompt_budget(16384, 1024) == floor(15360 * 0.9)`; invalid combos rejected.
* `test_schemas.py`: models accept valid, reject empty/over-length/extra fields; `provider_schema` output contains no `$ref`/`$defs`/`anyOf`, keeps `required`, types optional fields as plain strings, retains descriptions.
* `test_ollama_provider.py`: **`num_ctx` regression (named, locked):** every request body has `options.num_ctx == context_tokens` — test through every public call path; `format`, `temperature`, `seed`, `num_predict` present; non-JSON content → `MalformedOutput` with raw text truncated; preflight messages contain `ollama serve` / `ollama pull <model>` / the version requirement; bare model name matches `:latest`; loopback detection; transient retry honors injected sleep and stops at `max_transient_retries`.
* `test_openai_compat_provider.py`: preset application (all defaults, overrides win, missing `model` → clear `ConfigError`); request body for each `structured_output` mode; `seed` only with `send_seed`; `reasoning_effort` absent when unset; key from env at call time, missing → `AuthError` naming the variable; **the key never appears** in any log record, exception message, or captured request logging (assert by scanning); 401 not retried; 429 with `Retry-After` honored; 400 → `BadRequestError` with redacted body; code-fence/prose-wrapped JSON extracted; truncated output (`finish_reason == "length"`) → `MalformedOutput` mentioning truncation; preflight cases (401, 404/405/501 skip, connection error, model missing → warning).
* `test_consent.py`: local never prompts; remote + `--yes` proceeds; remote + config ack proceeds; remote + TTY asks and respects "n"; remote + no TTY → `ConsentRequired` with the fix in the message; loopback helper cases including `[::1]`.
* `test_doctor.py`: success report content; each failure prints the exact fix and exits 1; no writes (tree snapshot); no source file is opened (project with unreadable files still works); `--provider`/`--model` overrides; unknown provider exit 1.

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected result:

```text
all tests pass offline; no test touches a real network or model
```

## Hardware / Model-Dependent Checks

Record each as Passed / Failed / Not run (reason) in `logs/04-providers-budget-schemas.md`:

* `python scripts/provider_smoke.py` against local Ollama with the default model.
* `GEMINI_API_KEY=… python scripts/provider_smoke.py --provider gemini --model <id>`; include the verified parameter-support table and the reasoning-vs-`max_output_tokens` finding (§7 Requirement 3).
* `codewhy doctor` against each of: Ollama down, model not pulled, Gemini with a bad key, Gemini healthy.

## Manual Verification

1. Run `codewhy doctor` with Ollama stopped; confirm the message tells you exactly what to run.
2. Run `codewhy config show --provider gemini --model x` and confirm preset values are visible and no key appears.
3. Grep the repository, logs, and test output for your actual API key string; there must be zero hits.

---

# 9. Acceptance Criteria

* [ ] Both providers and the `gemini` preset behave per §6–§7; no default hosted model id exists in code.
* [ ] `num_ctx` is sent on every Ollama request (named regression test passes).
* [ ] The key never leaks (logs, errors, files, requests captured in tests).
* [ ] Consent logic blocks silent remote calls, including non-TTY runs.
* [ ] `provider_schema` produces endpoint-safe schemas; pass schemas validate as specified.
* [ ] `FakeProvider` and the budget-checking wrapper exist and are documented in the log for task 05.
* [ ] Live smoke results (or **Not run (reason)**) and the Gemini parameter/reasoning findings recorded; the architecture was corrected if live reality differed.
* [ ] `doctor` and `--provider`/`--model` work.
* [ ] No dependencies beyond `httpx`.
* [ ] `git diff` reviewed; work log updated.
* [ ] If a commit was authorized: staged diff reviewed, only intended files, no AI attribution, no push/PR.

---

# 10. Known Risks

* **Reasoning models can consume `max_output_tokens`**, producing truncated or empty JSON. This is the most likely cause of mysterious Gemini failures; the live check in Requirement 3 exists to settle it. If reasoning cannot be bounded, the fix may be a larger default `max_output_tokens` for the preset (update the architecture) rather than prompt changes.
* **Compat endpoints accept subsets.** An unsupported parameter can produce a 400 that looks like a code bug. Keep the request body minimal and make optional parameters genuinely optional.
* **Ollama version differences.** `format` with a schema needs a recent Ollama; the preflight must say so plainly rather than let users hit an opaque error.
* **Free-tier rate limits** make 429s routine on hosted use; the backoff must be correct and testable without sleeping.
* **Model ids and endpoint details change.** That is why nothing here hardcodes a Gemini model id and why the architecture is updated when live verification disagrees.
* `estimate_tokens` is a conservative approximation; it can both over- and under-estimate for unusual text. Never present its output to users as an exact count.

---

# 11. Open Questions

* **requires clarification:** Which Gemini model id will you use for development and for the README example? — Recommended: pick one current id at implementation time and keep it out of code and defaults; mention it only in README examples that are clearly dated.
* **requires clarification:** If reasoning tokens do eat the output cap, raise the preset's `max_output_tokens` or force minimal reasoning? — Recommended: whichever the live check shows is reliable; prefer a higher cap over silent truncation.
* **requires clarification:** Show Ollama's recommended minimum version in `doctor` output even on success? — Recommended: only on failure.

---

# 12. Handoff Notes

* Task 05 uses `Provider`, `FakeProvider` (+ budget-checking wrapper), `prompt_budget`, `estimate_tokens`, the three pass schemas, and `provider_schema` (indirectly via the provider).
* Task 06 wires `build_provider`, `ensure_remote_ack`, and `preflight` into `scan`.
* Task 07 includes `reasoning_effort` and `structured_output` in the cache `config_fp`.
* Record in the log: verified Ollama minimum version, Gemini parameter table, reasoning finding, preset corrections, and architecture edits.

If the task is complete:

> No outstanding handoff notes.

---

# 13. Definition of Done

See `CLAUDE.md` → *Definition of done*. Do not commit, push, open a PR, or merge unless the user explicitly authorizes that specific action.

## Final Agent Instruction

Before making changes: read `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/TESTS.md`, this file, and the logs of tasks 01–03; run `git status`; write your plan into `logs/04-providers-budget-schemas.md`.

During implementation: verify provider behavior against current docs and, where possible, live; stay in scope; never log or store the API key; never default a hosted model id.

If the task requires something not covered here, stop and ask the project lead.

Before handing off: run the tests, run the live smoke checks you can (record the rest as Not run), review `git diff` and `git status`, update the log, and report what changed, what was verified, and what is untested or unresolved.
