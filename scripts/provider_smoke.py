"""Live smoke check for the configured provider: preflight plus one real generation.

    python scripts/provider_smoke.py [--provider P] [--model M] [--path PATH] [--max-output-tokens N]

Run it in your own terminal. With a hosted provider, export the key there first
(e.g. GEMINI_API_KEY); this script never prints the key or the prompt body. It
sends only the small fixed prompt below, never your project's source.

It prints PASS/FAIL, the time taken, the request parameters that were sent
(names and non-secret values only), and the reply's finish reason and token
usage, which is what settles whether reasoning tokens eat `max_output_tokens`
(run once with the default cap and once with `--max-output-tokens 1024`).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
from pydantic import ValidationError

from codewhy.config import ConfigError, resolve_config
from codewhy.llm.provider import ProviderError, build_provider
from codewhy.summarize.schemas import FilePass, provider_schema

PROMPT = (
    "You are documenting a small Python project for a newcomer. The file `utils.py` defines "
    "`slugify(title: str) -> str`, which lowercases a title and joins its words with hyphens; "
    "`main.py` imports it to build URL paths for blog posts. Reply with a JSON object with a "
    "`purpose` field (1-2 sentences on why utils.py exists) and an optional `notes` field."
)
# Request fields whose values are safe and useful to show.
SHOWN_VALUES = ("model", "temperature", "max_tokens", "seed", "reasoning_effort", "stream")


def describe_request(body: dict) -> str:
    parts = [f"{key}={body[key]!r}" for key in SHOWN_VALUES if key in body]
    if "options" in body:
        parts.append("options=" + json.dumps(body["options"], sort_keys=True))
    if "format" in body:
        parts.append("format=<json schema>")
    if "response_format" in body:
        parts.append(f"response_format.type={body['response_format'].get('type')!r}")
    return ", ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--path", default=".", help="folder whose codewhy.toml to use (default: .)")
    parser.add_argument("--max-output-tokens", type=int, help="override llm.max_output_tokens for this run")
    args = parser.parse_args()

    overrides = {"llm.provider": args.provider, "llm.model": args.model, "llm.max_output_tokens": args.max_output_tokens}
    seen: dict[str, object] = {}

    def on_request(request: httpx.Request) -> None:
        if request.method == "POST":
            seen["request"] = describe_request(json.loads(request.content))

    def on_response(response: httpx.Response) -> None:
        if response.request.method != "POST":
            return
        response.read()
        try:
            data = response.json()
        except ValueError:
            return
        if isinstance(data, dict):
            choice = (data.get("choices") or [{}])[0]
            seen["finish"] = choice.get("finish_reason") or data.get("done_reason")
            seen["usage"] = data.get("usage") or {k: data[k] for k in ("prompt_eval_count", "eval_count") if k in data}

    try:
        config = resolve_config(Path(args.path), overrides)
        client = httpx.Client(event_hooks={"request": [on_request], "response": [on_response]})
        provider = build_provider(config, client=client)
    except ConfigError as exc:
        print(f"FAIL (config): {exc}")
        return 1

    llm = config.llm
    print(f"provider={llm.provider} model={llm.effective_model} endpoint={provider.endpoint_label}")
    started = time.monotonic()
    try:
        provider.preflight()
        print(f"preflight: ok ({time.monotonic() - started:.1f}s)")
        started = time.monotonic()
        result = provider.generate_json(
            PROMPT, provider_schema(FilePass), max_output_tokens=llm.effective_max_output_tokens
        )
        FilePass.model_validate(result)
    except (ProviderError, ValidationError) as exc:
        print(f"FAIL after {time.monotonic() - started:.1f}s: {type(exc).__name__}: {exc}")
        print(f"sent: {seen.get('request', '(no generation request sent)')}")
        print(f"finish_reason: {seen.get('finish')}  usage: {json.dumps(seen.get('usage'), sort_keys=True)}")
        return 1
    finally:
        client.close()
    print(f"PASS in {time.monotonic() - started:.1f}s")
    print(f"sent: {seen.get('request')}")
    print(f"finish_reason: {seen.get('finish')}  usage: {json.dumps(seen.get('usage'), sort_keys=True)}")
    print(f"purpose: {result.get('purpose')!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
