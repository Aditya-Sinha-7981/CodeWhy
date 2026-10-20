from ...config import DEFAULT_TIMEOUT
from ..services.service import service


def routes() -> str:
    return f"{service()}:{DEFAULT_TIMEOUT}"

