"""Liveness API and same-origin development simulator."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.types import Scope
from starlette.concurrency import run_in_threadpool
from typing import Literal

from .config import Settings
from .database import Database
from .auth import router as auth_router
from .devices import router as devices_router
from .enrollment import router as enrollment_router


class Health(BaseModel):
    status: Literal["ok"] = "ok"


class WebFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if path.endswith('.mjs') and response.status_code == 200:
            # Windows MIME mappings may classify modules as text/plain.
            # Browsers require a JavaScript MIME type for ES modules.
            response.headers['content-type'] = 'text/javascript; charset=utf-8'
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    database = Database(settings.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await run_in_threadpool(database.initialize)
        yield

    app = FastAPI(title="Storm OS", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # Default validation errors can echo request input, including passwords.
        return JSONResponse(status_code=422, content={
            "detail": [{"loc": item['loc'], "type": item['type'], "msg": item['msg']}
                       for item in error.errors()]
        })

    app.include_router(auth_router)
    app.include_router(enrollment_router)
    app.include_router(devices_router)

    @app.get("/health", response_model=Health)
    def health(response: Response) -> Health:
        # Liveness only: no database, agent or hardware checks are implied.
        response.headers["Cache-Control"] = "no-store"
        return Health()

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse("/simulator/")

    simulator = Path(__file__).resolve().parents[2] / "web" / "simulator"
    app.mount("/simulator", WebFiles(directory=simulator, html=True), name="simulator")
    panel = simulator.parent / "panel"
    app.mount("/panel", WebFiles(directory=panel, html=True), name="panel")
    return app
