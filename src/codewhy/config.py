"""Load, validate, and layer `codewhy.toml` configuration.

Layering is defaults < `codewhy.toml` < CLI overrides. Every problem found in
one pass is reported together in a single `ConfigError`.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)

CONFIG_FILE_NAME = "codewhy.toml"

ProviderName = Literal["ollama", "openai-compat", "gemini"]
StructuredOutput = Literal["schema", "json", "prompt"]
OutputMode = Literal["single-file", "docs-tree", "both"]
Language = Literal["python", "javascript", "typescript"]

DEFAULT_OLLAMA_MODEL = "qwen2.5-coder:14b"
DEFAULT_CONTEXT_TOKENS: dict[str, int] = {
    "ollama": 16384,
    "openai-compat": 32768,
    "gemini": 32768,
}
# Gemini counts thinking tokens against the output cap (docs, 2026-10-14), and
# reasoning_effort "low" alone can use 1024 of them, so its default is larger.
DEFAULT_MAX_OUTPUT_TOKENS: dict[str, int] = {
    "ollama": 1024,
    "openai-compat": 1024,
    "gemini": 8192,
}

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
# Values the gemini preset fills in when the user left the key unset. `model`
# is deliberately absent: hosted model ids go stale, so none is shipped.
GEMINI_PRESET: dict[str, object] = {
    "base_url": GEMINI_BASE_URL,
    "api_key_env": "GEMINI_API_KEY",
    "structured_output": "schema",
    "reasoning_effort": "low",
    "send_seed": False,
    "context_tokens": DEFAULT_CONTEXT_TOKENS["gemini"],
    "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS["gemini"],
}


class ConfigError(Exception):
    """One or more configuration problems; the message lists all of them."""

    def __init__(self, problems: list[str], *, source: str | None = None) -> None:
        self.problems = list(problems)
        self.source = source
        where = f" in {source}" if source else ""
        if len(self.problems) == 1:
            message = f"invalid configuration{where}: {self.problems[0]}"
        else:
            lines = "\n".join(f"  - {p}" for p in self.problems)
            message = f"invalid configuration{where} ({len(self.problems)} problems):\n{lines}"
        super().__init__(message)


def _check_relative_posix(value: str) -> str:
    """Validate a project-relative POSIX path and return it without `./` or trailing `/`."""
    if not value or not value.strip():
        raise ValueError("must not be empty")
    if "\\" in value:
        raise ValueError("must use '/' as the path separator")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute() or PureWindowsPath(value).drive:
        raise ValueError("must be a relative path inside the project")
    parts = [p for p in PurePosixPath(value).parts if p != "."]
    if ".." in parts:
        raise ValueError("must not contain '..'")
    if not parts:
        raise ValueError("must name a file or folder, not the project root")
    return "/".join(parts)


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class LLMConfig(_Section):
    provider: ProviderName = "ollama"
    # None means "not set": ollama falls back to DEFAULT_OLLAMA_MODEL, hosted
    # providers have no default (task 04 reports the missing model).
    model: str | None = None
    host: str = "http://localhost:11434"
    base_url: str | None = None
    api_key_env: str | None = None
    # None means "use the provider-specific default" (see effective_context_tokens).
    context_tokens: int | None = Field(default=None, gt=0)
    # None means "use the provider-specific default" (see effective_max_output_tokens).
    max_output_tokens: int | None = Field(default=None, gt=0)
    temperature: float = Field(default=0.0, ge=0, le=2)
    seed: int = 0
    timeout_seconds: int = Field(default=300, gt=0)
    max_transient_retries: int = Field(default=3, ge=0)
    # The next three are None when unset so presets (task 04) can tell an
    # explicit user value from a default.
    structured_output: StructuredOutput | None = None
    send_seed: bool | None = None
    reasoning_effort: str | None = None
    acknowledge_remote: bool = False

    @property
    def effective_context_tokens(self) -> int:
        if self.context_tokens is not None:
            return self.context_tokens
        return DEFAULT_CONTEXT_TOKENS[self.provider]

    @property
    def effective_max_output_tokens(self) -> int:
        if self.max_output_tokens is not None:
            return self.max_output_tokens
        return DEFAULT_MAX_OUTPUT_TOKENS[self.provider]

    @property
    def effective_model(self) -> str | None:
        if self.model is not None:
            return self.model
        return DEFAULT_OLLAMA_MODEL if self.provider == "ollama" else None

    @model_validator(mode="after")
    def _output_fits_context(self) -> LLMConfig:
        output, context = self.effective_max_output_tokens, self.effective_context_tokens
        if output >= context:
            raise ValueError(
                f"max_output_tokens ({output}) must be smaller than context_tokens ({context}); "
                "the prompt needs the rest of the context window"
            )
        return self


class OutputConfig(_Section):
    mode: OutputMode = "single-file"
    dir: str = "codewhy-docs"
    single_file_name: str = "PROJECT_DOCS.md"
    token_budget: int = Field(default=20000, gt=0)

    @field_validator("dir", "single_file_name")
    @classmethod
    def _relative(cls, value: str) -> str:
        return _check_relative_posix(value)

    @model_validator(mode="after")
    def _no_collision(self) -> OutputConfig:
        a, b = self.dir, self.single_file_name
        if a == b or b.startswith(a + "/") or a.startswith(b + "/"):
            raise ValueError(f"dir ({a!r}) and single_file_name ({b!r}) must not overlap")
        return self


class ExtractionConfig(_Section):
    languages: list[Language] = Field(default_factory=lambda: ["python", "javascript", "typescript"])
    max_file_bytes: int = Field(default=500_000, gt=0)


class ProjectConfig(_Section):
    entry_point: str | None = None

    @field_validator("entry_point")
    @classmethod
    def _relative(cls, value: str | None) -> str | None:
        return None if value is None else _check_relative_posix(value)


class Config(_Section):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    project: ProjectConfig = Field(default_factory=ProjectConfig)

    _source_path: Path | None = PrivateAttr(default=None)

    @property
    def source_path(self) -> Path | None:
        """The `codewhy.toml` this config was read from, or None for defaults only."""
        return self._source_path


SECTION_MODELS: dict[str, type[BaseModel]] = {
    "llm": LLMConfig,
    "output": OutputConfig,
    "extraction": ExtractionConfig,
    "project": ProjectConfig,
}


@dataclass(frozen=True)
class RawConfig:
    """The validated contents of `codewhy.toml` exactly as written (no defaults filled in)."""

    data: dict[str, dict[str, Any]] = field(default_factory=dict)
    source_path: Path | None = None


def _describe(loc: tuple[int | str, ...]) -> str:
    if not loc:
        return "config"
    head = f"[{loc[0]}]"
    rest = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in loc[1:])
    return head + rest


def _format_errors(exc: ValidationError) -> list[str]:
    problems: list[str] = []
    for err in exc.errors(include_url=False):
        loc = tuple(err["loc"])
        if err["type"] == "extra_forbidden":
            problems.append(_unknown_key_message(loc))
        else:
            msg = err["msg"]
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, ") :]
            problems.append(f"{_describe(loc)}: {msg}")
    return problems


def _unknown_key_message(loc: tuple[int | str, ...]) -> str:
    if len(loc) == 1:
        name = str(loc[0])
        candidates = list(SECTION_MODELS)
        what = f"unknown section [{name}]"
    else:
        name = str(loc[-1])
        model = SECTION_MODELS.get(str(loc[0]))
        candidates = list(model.model_fields) if model else []
        what = f"unknown key {_describe(loc)}"
    matches = difflib.get_close_matches(name, candidates, n=1)
    return f'{what} (did you mean "{matches[0]}"?)' if matches else what


def _validate(data: dict[str, Any], *, source: str | None) -> Config:
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_errors(exc), source=source) from None


_TOML_LOCATION = re.compile(r"\(at line (\d+), column (\d+)\)")


def load_config(root: Path) -> RawConfig:
    """Read and validate `<root>/codewhy.toml`; an absent file yields an empty RawConfig."""
    path = root / CONFIG_FILE_NAME
    if not path.is_file():
        return RawConfig()
    source = str(path)
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except OSError as exc:
        raise ConfigError([f"cannot read file: {exc.strerror or exc}"], source=source) from None
    except UnicodeDecodeError:
        raise ConfigError(["file is not valid UTF-8"], source=source) from None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        lineno = getattr(exc, "lineno", None)
        colno = getattr(exc, "colno", None)
        msg = getattr(exc, "msg", None) or _TOML_LOCATION.sub("", str(exc)).strip()
        if lineno is None:
            m = _TOML_LOCATION.search(str(exc))
            if m:
                lineno, colno = int(m.group(1)), int(m.group(2))
        where = f"line {lineno}, column {colno}" if lineno is not None else "unknown location"
        raise ConfigError([f"invalid TOML at {where}: {msg}"], source=source) from None
    _validate(data, source=source)
    return RawConfig(data=data, source_path=path)


def _apply_overrides(data: dict[str, Any], overrides: dict[str, object]) -> dict[str, Any]:
    merged: dict[str, Any] = {k: dict(v) if isinstance(v, dict) else v for k, v in data.items()}
    for dotted, value in sorted(overrides.items()):
        if value is None:
            continue
        section, _, key = dotted.partition(".")
        if not key:
            raise ValueError(f"override key must be 'section.key', got {dotted!r}")
        table = merged.setdefault(section, {})
        if not isinstance(table, dict):
            table = merged[section] = {}
        table[key] = value
    return merged


def resolve_config(root: Path, overrides: dict[str, object] | None = None) -> Config:
    """Defaults < `codewhy.toml` < overrides (dotted keys; None means not provided)."""
    raw = load_config(root)
    merged = _apply_overrides(raw.data, overrides or {})
    # The file alone already validated, so anything failing now involves an override.
    source = "command-line options" if raw.source_path is None else f"{raw.source_path} + command-line options"
    config = apply_preset(_validate(merged, source=source))
    config._source_path = raw.source_path
    return config


def apply_preset(config: Config) -> Config:
    """Fill the gemini preset into every llm key the user left unset; explicit values win."""
    if config.llm.provider != "gemini":
        return config
    filled = {key: value for key, value in GEMINI_PRESET.items() if getattr(config.llm, key) is None}
    if not filled:
        return config
    return config.model_copy(update={"llm": config.llm.model_copy(update=filled)})


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        # JSON string escapes are a subset of TOML basic-string escapes.
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    raise TypeError(f"cannot render {type(value).__name__} as TOML")


def render_toml(config: Config) -> str:
    """Serialize a Config to TOML; unset (None) keys appear as comments."""
    lines: list[str] = []
    for section in SECTION_MODELS:
        values = getattr(config, section).model_dump()
        if lines:
            lines.append("")
        lines.append(f"[{section}]")
        for key, value in values.items():
            if value is None:
                lines.append(f"# {key} is not set")
            else:
                lines.append(f"{key} = {_toml_value(value)}")
    return "\n".join(lines) + "\n"
