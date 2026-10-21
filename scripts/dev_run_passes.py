"""Run the summarization passes on a real project and print the results as JSON.

    python scripts/dev_run_passes.py PATH [--provider P] [--model M] [--yes]

For manual quality checks of prompts and outputs. It prints every pass result,
the number of provider calls, and the estimated tokens of each prompt (never
the prompt text). With a remote provider your source code is sent to it, so the
script refuses to run without --yes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from codewhy.config import ConfigError, resolve_config
from codewhy.llm.budget import estimate_tokens
from codewhy.llm.provider import Provider, ProviderError, build_provider
from codewhy.pipeline import run_pipeline
from codewhy.project import build_project
from codewhy.summarize.result import PassResult, pass_budget


class CountingProvider:
    """Delegates to a real provider and records each prompt's estimated size."""

    def __init__(self, inner: Provider) -> None:
        self.inner = inner
        self.is_remote = inner.is_remote
        self.endpoint_label = inner.endpoint_label
        self.prompt_tokens: list[int] = []

    def preflight(self) -> None:
        self.inner.preflight()

    def generate_json(
        self, prompt: str, schema: dict, *, max_output_tokens: int, temperature: float | None = None
    ) -> dict:
        self.prompt_tokens.append(estimate_tokens(prompt))
        return self.inner.generate_json(prompt, schema, max_output_tokens=max_output_tokens, temperature=temperature)


def as_json(result: PassResult) -> dict:
    return {
        "data": result.data.model_dump() if result.data is not None else None,
        "failure": result.failure,
        "generated_at": result.generated_at.isoformat(),
        "metadata": result.metadata,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path)
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--yes", action="store_true", help="allow sending source code to a remote provider")
    args = parser.parse_args()

    try:
        config = resolve_config(args.path, {"llm.provider": args.provider, "llm.model": args.model})
        provider = CountingProvider(build_provider(config))
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if provider.is_remote and not args.yes:
        print(
            f"error: this would send the source code in {args.path} to {provider.endpoint_label}; "
            "re-run with --yes to allow it",
            file=sys.stderr,
        )
        return 1
    try:
        provider.preflight()
    except ProviderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    project = build_project(args.path, config)
    started = time.monotonic()

    def progress(pass_name: str, subject: str, status: str) -> None:
        print(f"{pass_name:8} {subject or '(project)'}: {status} ({time.monotonic() - started:.1f}s)", file=sys.stderr)

    try:
        result = run_pipeline(project, provider, config, clock=lambda: datetime.now(UTC), on_item=progress)
    except ProviderError as exc:
        print(f"error: run aborted: {exc}", file=sys.stderr)
        return 1
    finally:
        close = getattr(provider.inner, "close", None)
        if close is not None:
            close()

    report = {
        "provider": config.llm.provider,
        "model": config.llm.effective_model,
        "pass_budget_tokens": pass_budget(config),
        "calls": len(provider.prompt_tokens),
        "prompt_tokens_estimated": provider.prompt_tokens,
        "seconds": round(time.monotonic() - started, 1),
        "counts": result.counts,
        "files": {path: as_json(item) for path, item in result.files.items()},
        "modules": {path: as_json(item) for path, item in result.modules.items()},
        "project": as_json(result.project),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
