---
generated_by: codewhy vtest
source: solver.py
generated_at: 2026-01-01T12:00:00Z
---

# `solver.py`

## Purpose

Holds the backtracking search that main.py calls to place the queens.

## Depends On

- `utils.py`

## Used By

- `main.py`

## Defined Here

- `class Board`
  - `def __init__(self, size: int) -> None`
- `def solve(size: int) -> list[int]`

## Notes

solve imports is_safe lazily, so it returns an empty board if utils lacks it.
