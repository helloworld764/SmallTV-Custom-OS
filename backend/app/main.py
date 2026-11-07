"""Liveness API and same-origin development simulator."""

from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Literal

from .config import Settings


class Health(BaseModel):
    status: Literal["ok"] = "ok"


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
    app.mount("/simulator", StaticFiles(directory=simulator, html=True), name="simulator")
    return app
