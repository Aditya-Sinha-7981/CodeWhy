from .db import connect


def application() -> str:
    return connect()
