---
generated_by: codewhy vtest
source: utils.py
generated_at: 2026-01-01T12:00:00Z
---

# `utils.py`

## Purpose

Provides the board helpers the solver and main.py share. It keeps the search code small.

## Depends On

- None

## Used By

- `main.py`
- `solver.py`

## Defined Here

- `def board_size() -> int`
- `def available_columns(size: int) -> list[int]`
- `def is_safe(board: list[int], column: int) -> bool`
- `def place_queen(board: list[int], columns: list[int], safe) -> list[int]`
