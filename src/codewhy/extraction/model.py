"""Immutable, deterministic structures emitted by the extraction layer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, TypeAlias


def _tuple(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(values)


FunctionKind: TypeAlias = Literal["function", "class", "type", "module"]
TypeKind: TypeAlias = Literal["interface", "type", "enum"]
Status: TypeAlias = Literal["ok", "skipped"]


@dataclass(frozen=True)
class FunctionInfo:
    name: str
    signature: str
    docstring: str | None
    start_line: int
    end_line: int
    is_async: bool
    decorators: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "decorators", _tuple(self.decorators))

    def to_dict(self) -> dict[str, object]:
        return {
            "decorators": list(self.decorators),
            "docstring": self.docstring,
            "end_line": self.end_line,
            "is_async": self.is_async,
            "name": self.name,
            "signature": self.signature,
            "start_line": self.start_line,
        }


@dataclass(frozen=True)
class ClassInfo:
    name: str
    bases: tuple[str, ...]
    docstring: str | None
    methods: tuple[FunctionInfo, ...]
    start_line: int
    end_line: int
    decorators: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "bases", _tuple(self.bases))
        object.__setattr__(self, "methods", tuple(self.methods))
        object.__setattr__(self, "decorators", _tuple(self.decorators))

    def to_dict(self) -> dict[str, object]:
        return {
            "bases": list(self.bases),
            "decorators": list(self.decorators),
            "docstring": self.docstring,
            "end_line": self.end_line,
            "methods": [method.to_dict() for method in self.methods],
            "name": self.name,
            "start_line": self.start_line,
        }


@dataclass(frozen=True)
class TypeInfo:
    name: str
    kind: TypeKind
    start_line: int
    end_line: int

    def to_dict(self) -> dict[str, object]:
        return {
            "end_line": self.end_line,
            "kind": self.kind,
            "name": self.name,
            "start_line": self.start_line,
        }


@dataclass(frozen=True)
class ImportInfo:
    kind: Literal["python", "js"]
    module: str
    names: tuple[str, ...]
    level: int
    line: int
    is_type_only: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "names", _tuple(self.names))

    def to_dict(self) -> dict[str, object]:
        return {
            "is_type_only": self.is_type_only,
            "kind": self.kind,
            "level": self.level,
            "line": self.line,
            "module": self.module,
            "names": list(self.names),
        }


@dataclass(frozen=True)
class UnitInfo:
    name: str
    kind: FunctionKind
    start_line: int
    end_line: int
    children: tuple[UnitInfo, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "children", tuple(self.children))

    def to_dict(self) -> dict[str, object]:
        return {
            "children": [child.to_dict() for child in self.children],
            "end_line": self.end_line,
            "kind": self.kind,
            "name": self.name,
            "start_line": self.start_line,
        }


@dataclass(frozen=True)
class FileStructure:
    path: str
    language: str
    source_text: str
    line_count: int
    functions: tuple[FunctionInfo, ...] = ()
    classes: tuple[ClassInfo, ...] = ()
    types: tuple[TypeInfo, ...] = ()
    imports: tuple[ImportInfo, ...] = ()
    top_level_units: tuple[UnitInfo, ...] = ()
    status: Status = "ok"
    warning: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "functions", tuple(self.functions))
        object.__setattr__(self, "classes", tuple(self.classes))
        object.__setattr__(self, "types", tuple(self.types))
        object.__setattr__(self, "imports", tuple(self.imports))
        object.__setattr__(self, "top_level_units", tuple(self.top_level_units))

    def to_dict(self, include_source: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "classes": [item.to_dict() for item in self.classes],
            "functions": [item.to_dict() for item in self.functions],
            "imports": [item.to_dict() for item in self.imports],
            "language": self.language,
            "line_count": self.line_count,
            "path": self.path,
            "status": self.status,
            "top_level_units": [item.to_dict() for item in self.top_level_units],
            "types": [item.to_dict() for item in self.types],
            "warning": self.warning,
        }
        if include_source:
            result["source_text"] = self.source_text
        return {key: result[key] for key in sorted(result)}

    def to_canonical_json(self, include_source: bool = True) -> str:
        return json.dumps(
            self.to_dict(include_source=include_source),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
