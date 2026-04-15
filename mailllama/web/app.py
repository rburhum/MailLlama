"""FastAPI app."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import get_settings
from ..ssh_tunnel import maybe_ssh_tunnel
from .routes import actions, auth, dashboard, rules, senders, sizes, subscriptions, tasks

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _format_bytes(n: int | None) -> str:
    if not n:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    value = float(n)
    while value >= 1024 and i < len(units) - 1:
        value /= 1024
        i += 1
    return f"{value:,.1f} {units[i]}"


templates.env.filters["bytes"] = _format_bytes


class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        token = settings.web_auth_token
        if not token:
            return await call_next(request)
        # Allow OAuth callback & static
        if request.url.path.startswith("/auth/") or request.url.path.startswith("/static/"):
            return await call_next(request)
        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer ") or auth_header[7:] != token:
            return await call_next(request) if request.url.path == "/health" else (
                _unauthorized()
            )
        return await call_next(request)


def _unauthorized():
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse("unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Open an SSH tunnel (if configured) for the life of the server."""
    with maybe_ssh_tunnel() as tunnel:
        if tunnel.spawned:
            log.info("SSH tunnel active: 127.0.0.1:%d -> %s", tunnel.local_port, tunnel.remote)
        yield


def create_app() -> FastAPI:
    app = FastAPI(title="MailLlama", version="0.1.0", lifespan=_lifespan)

    app.add_middleware(BearerAuthMiddleware)

    static_dir = BASE_DIR / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(dashboard.router)
    app.include_router(auth.router, prefix="/auth")
    app.include_router(senders.router, prefix="/senders")
    app.include_router(subscriptions.router, prefix="/subscriptions")
    app.include_router(sizes.router, prefix="/sizes")
    app.include_router(rules.router, prefix="/rules")
    app.include_router(actions.router, prefix="/actions")
    app.include_router(tasks.router, prefix="/tasks")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()


def get_templates() -> Jinja2Templates:
    return templates
