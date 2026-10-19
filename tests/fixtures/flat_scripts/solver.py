from utils import available_columns, place_queen


class Board:
    """Mutable backtracking board."""

    def __init__(self, size: int) -> None:
        self.size = size


def solve(size: int) -> list[int]:
    """Return one safe placement for an N-Queens board."""
    board: list[int] = []
    try:
        from utils import is_safe
    except ImportError:
        return board
    return place_queen(board, available_columns(size), is_safe)
