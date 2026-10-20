import os

from .utils import helper
from app.services.service import service


def app() -> str:
    return helper() + service()

