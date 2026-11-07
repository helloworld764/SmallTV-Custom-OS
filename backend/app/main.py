"""Liveness API and same-origin development simulator."""

from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.types import Scope
from typing import Literal

from .config import Settings


class Health(BaseModel):
    status: Literal["ok"] = "ok"


class SimulatorFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if path.endswith('.mjs') and response.status_code == 200:
            # Windows MIME mappings may classify modules as text/plain.
            # Browsers require a JavaScript MIME type for ES modules.
            response.headers['content-type'] = 'text/javascript; charset=utf-8'
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="SmallTV Custom OS", version="0.1.0")
    app.state.settings = settings if settings is not None else Settings()

    @app.get("/health", response_model=Health)
    def health(response: Response) -> Health:
        # Liveness only: no database, agent or hardware checks are implied.
        response.headers["Cache-Control"] = "no-store"
        return Health()

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse("/simulator/")

    simulator = Path(__file__).resolve().parents[2] / "web" / "simulator"
    app.mount("/simulator", SimulatorFiles(directory=simulator, html=True), name="simulator")
    return app
