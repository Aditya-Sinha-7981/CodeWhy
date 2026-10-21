---
generated_by: codewhy vtest
source: .
generated_at: 2026-01-01T12:00:00Z
---

# `(project root)`

## Responsibility

Contains the whole N-Queens example. Everything lives in the project root.

## How the pieces cooperate

main.py drives solver.py, and both rely on the helpers in utils.py.

## Files in this module

- `main.py`
- `solver.py`
- `utils.py`

## Internal dependencies

- `main.py` → `solver.py`
- `main.py` → `utils.py`
- `solver.py` → `utils.py`

## External dependencies

- None
