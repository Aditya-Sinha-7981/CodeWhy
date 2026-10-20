from ..core import app
from ..utils import helper


def service() -> str:
    return helper() + app.__name__

