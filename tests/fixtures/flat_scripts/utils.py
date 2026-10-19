def board_size() -> int:
    """Use a small board for the example."""
    return 4


def available_columns(size: int) -> list[int]:
    return list(range(size))


def is_safe(board: list[int], column: int) -> bool:
    return all(abs(row - previous) != abs(column - placed) for row, placed in enumerate(board) for previous in [row])


def place_queen(board: list[int], columns: list[int], safe) -> list[int]:
    if not columns:
        return board
    for column in columns:
        if safe(board, column):
            answer = place_queen(board + [column], [item for item in columns if item != column], safe)
            if len(answer) == len(board) + 1:
                return answer
    return board
