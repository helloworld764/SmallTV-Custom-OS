"""Run from the repository root: python -m backend.app."""

import uvicorn

from .config import Settings
from .main import create_app


def main() -> None:
    settings = Settings()
    uvicorn.run(
        create_app(settings), host=settings.host, port=settings.port, log_level=settings.log_level
    )


if __name__ == "__main__":
    main()
