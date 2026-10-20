import app.api.routes
from plugins import registry

from .services import service


def run() -> None:
    app.api.routes.routes()
    service.service()
    registry.PLUGINS["default"]("https://example.invalid")
