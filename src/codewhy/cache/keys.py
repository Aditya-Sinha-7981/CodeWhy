"""Pure, content-addressed cache-key helpers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

# Bump when prompt templates, pass schemas, extraction output, chunking,
# overflow-ladder behavior, or PROMPT_VERSION changes.
PIPELINE_VERSION = 1
FAILED_MEMBER = "__failed__"


def canonical_json(value: object) -> str:
    """Return the stable JSON representation used as a cache hash input."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(value: str | bytes) -> str:
    """Hash UTF-8 text or bytes and return a lowercase hexadecimal digest."""
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _key(pass_name: str, config_fp: str, inputs: object) -> str:
    return sha256_hex(
        canonical_json(
            {"pass": pass_name, "PIPELINE_VERSION": PIPELINE_VERSION, "config_fp": config_fp, "inputs": inputs}
        )
    )


def config_fingerprint(config: Mapping[str, Any]) -> str:
    """Hash exactly the LLM settings that affect generated prose."""
    fields = (
        "provider",
        "model",
        "context_tokens",
        "max_output_tokens",
        "temperature",
        "seed",
        "reasoning_effort",
        "structured_output",
    )
    return sha256_hex(canonical_json({field: config.get(field) for field in fields}))


def file_key(
    config_fp: str,
    path: str,
    source_text: str,
    imports: Sequence[str],
    imported_by: Sequence[str],
) -> str:
    inputs = {
        "path": path,
        "source_text": source_text,
        "imports": sorted(imports),
        "imported_by": sorted(imported_by),
    }
    return _key("file", config_fp, inputs)


def module_key(
    config_fp: str,
    module: str,
    files: Sequence[Sequence[str]],
    edges: str,
    external: Sequence[str],
    omitted: int = 0,
) -> str:
    members = sorted([[str(path), str(text)] for path, text in files], key=lambda item: item[0])
    inputs = {
        "module": module,
        "files": members,
        "omitted": omitted,
        "edges": edges,
        "external": sorted(external),
    }
    return _key("module", config_fp, inputs)


def project_key(
    config_fp: str,
    entry_point: str | None,
    modules: Sequence[Sequence[str]],
    project_edges: str,
    other_files: Sequence[str],
) -> str:
    module_values = sorted([[str(path), str(text)] for path, text in modules], key=lambda item: item[0])
    inputs = {
        "entry_point": entry_point,
        "modules": module_values,
        "project_edges": project_edges,
        "other_files": sorted(other_files),
    }
    return _key("project", config_fp, inputs)
