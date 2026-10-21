"""Pass output schemas and the provider-facing schema simplifier.

The pydantic models validate every model response, whatever structured-output
mode the provider used. Field descriptions are instructions to the model (task
05's prompts rely on them), so keep them short and concrete.

`provider_schema` turns a model into the subset hosted endpoints accept: no
`$ref`/`$defs`, no `anyOf`, optional fields typed as plain strings, no titles,
defaults, or length limits (pydantic still enforces those afterwards).
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

# Generous caps: they exist to catch runaway output, not to shape prose.
FILE_TEXT_MAX = 1200
MODULE_TEXT_MAX = 1500
PROJECT_TEXT_MAX = 2500


# Required strings: stripped, non-empty, capped.
FileText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=FILE_TEXT_MAX)]
ModuleText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MODULE_TEXT_MAX)]
ProjectText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=PROJECT_TEXT_MAX)]
OptionalFileText = Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=FILE_TEXT_MAX)]


class _PassModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FilePass(_PassModel):
    purpose: FileText = Field(
        description=(
            "1-3 sentences on why this file exists in this project: the problem it solves "
            "and the role it plays for the files that import it. Not a list of its functions."
        )
    )
    notes: OptionalFileText = Field(
        default=None,
        description="Optional: one non-obvious fact a newcomer should know (a caveat, side effect, or quirk). Empty if none.",
    )

    @field_validator("notes")
    @classmethod
    def _empty_notes_are_none(cls, value: str | None) -> str | None:
        return value or None


class ModulePass(_PassModel):
    responsibility: ModuleText = Field(
        description="What this folder is responsible for in the project, in 1-3 sentences."
    )
    how_pieces_cooperate: ModuleText = Field(
        description="How the files in this folder work together and with the rest of the project, in plain prose."
    )


class ProjectPass(_PassModel):
    overview: ProjectText = Field(
        description="What the project does and who it is for, in one short paragraph."
    )
    architecture: ProjectText = Field(
        description="How the main parts of the project fit together and how a request or run flows through them."
    )


# Keys a provider-facing schema may keep; everything else is dropped.
_KEPT_KEYS = ("type", "description", "properties", "required", "items", "enum")


def provider_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Endpoint-safe JSON schema for `model` (see module docstring)."""
    full = model.model_json_schema()
    return _simplify(full, full.get("$defs", {}))


def _simplify(node: Any, defs: dict[str, Any]) -> Any:
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        merged = {**defs[ref.removeprefix("#/$defs/")], **{k: v for k, v in node.items() if k != "$ref"}}
        return _simplify(merged, defs)
    variants = node.get("anyOf")
    if isinstance(variants, list):
        non_null = [v for v in variants if not (isinstance(v, dict) and v.get("type") == "null")]
        # `str | None` becomes a plain string; any other union keeps its first branch.
        chosen = _simplify(non_null[0] if non_null else {"type": "string"}, defs)
        outer = {k: v for k, v in node.items() if k in _KEPT_KEYS and k != "type"}
        return {**chosen, **outer}
    result: dict[str, Any] = {}
    for key in _KEPT_KEYS:
        if key not in node:
            continue
        value = node[key]
        if key == "properties":
            value = {name: _simplify(prop, defs) for name, prop in value.items()}
        elif key == "items":
            value = _simplify(value, defs)
        elif key == "required":
            value = list(value)
        result[key] = value
    return result
