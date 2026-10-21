---
generated_by: codewhy vtest
source: project
generated_at: 2026-01-01T12:00:00Z
---

# flat_scripts — codebase documentation

This document describes a codebase. It was generated automatically by
codewhy from a local folder: structural facts (definitions, dependencies,
entry point) come directly from parsing the code, while the explanations were
written by a language model and may contain mistakes. All paths are relative
to the project root. Sections: Overview, Modules, Files.

## Overview

A small command-line example that solves the N-Queens puzzle by backtracking.

### Architecture

main.py calls solve in solver.py, which places queens using the checks in utils.py.

### Entry Point

`main.py`

### Modules

- `(project root)` — Contains the whole N-Queens example.

### Most depended-on files

- `utils.py` — imported by 2 file(s)
- `solver.py` — imported by 1 file(s)

## Modules

### `(project root)`

#### Responsibility

Contains the whole N-Queens example. Everything lives in the project root.

#### How the pieces cooperate

main.py drives solver.py, and both rely on the helpers in utils.py.

#### Files in this module

- `main.py`
- `solver.py`
- `utils.py`

#### Internal dependencies

- `main.py` → `solver.py`
- `main.py` → `utils.py`
- `solver.py` → `utils.py`

#### External dependencies

- None

## Files

### `main.py`

#### Purpose

Runs the example: it asks the solver for a placement on a small board and prints it.

#### Depends On

- `solver.py`
- `utils.py`

#### Used By

- None

#### Defined Here

- `def main() -> list[int]`

### `solver.py`

#### Purpose

Holds the backtracking search that main.py calls to place the queens.

#### Depends On

- `utils.py`

#### Used By

- `main.py`

#### Defined Here

- `class Board`
  - `def __init__(self, size: int) -> None`
- `def solve(size: int) -> list[int]`

#### Notes

solve imports is_safe lazily, so it returns an empty board if utils lacks it.

### `utils.py`

#### Purpose

Provides the board helpers the solver and main.py share. It keeps the search code small.

#### Depends On

- None

#### Used By

- `main.py`
- `solver.py`

#### Defined Here

- `def board_size() -> int`
- `def available_columns(size: int) -> list[int]`
- `def is_safe(board: list[int], column: int) -> bool`
- `def place_queen(board: list[int], columns: list[int], safe) -> list[int]`
