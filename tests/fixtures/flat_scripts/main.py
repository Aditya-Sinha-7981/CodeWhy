"""Command-line entry point for the solver."""

import utils
from solver import solve


def main() -> list[int]:
    """Solve a small board and format the answer."""
    return solve(utils.board_size())


if __name__ == "__main__":
    print(main())
