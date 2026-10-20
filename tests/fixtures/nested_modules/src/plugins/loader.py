import requests

from app.core import app


def load(url: str) -> str:
    return requests.get(url).text + app()
