from app.api.routes import routes
from app.core import app


def main() -> str:
    return routes() + app()

