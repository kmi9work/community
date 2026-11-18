"""Приложение FastAPI: API админки, webhook ботов, PWA-админка."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from . import logs, metrics
from .config import settings
from .db import get_engine, session_scope
from .logs import setup_logging


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_engine()
    from .seed import seed

    try:
        with session_scope() as s:
            seed(s)
    except Exception:  # noqa: BLE001 — база ещё без миграций
        logs.error("startup.seed_failed")
    yield


def create_app() -> FastAPI:
    setup_logging("api")
    app = FastAPI(title="Community CRM", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    from .api import routes_actions, routes_bot, routes_core

    app.include_router(routes_core.router)
    app.include_router(routes_actions.router)
    app.include_router(routes_actions.media_router)
    app.include_router(routes_bot.router)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        started = time.monotonic()
        path = request.url.path
        # защита от CSRF: изменяющие запросы API — только из своей админки (заголовок ставит fetch)
        if path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS") \
                and request.headers.get("x-requested-with") != "fetch":
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        response = await call_next(request)
        route = request.scope.get("route")
        template = getattr(route, "path", "other")
        elapsed = time.monotonic() - started
        metrics.REQUEST_SECONDS.labels(request.method, template, str(response.status_code)).observe(elapsed)
        if path not in ("/healthz", "/metrics") and not path.startswith("/assets/"):
            logs.event("http.request", method=request.method, path=template, code=response.status_code,
                       duration_ms=int(elapsed * 1000))
        if path.startswith("/api/") or path.startswith("/media/"):
            response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, session_cookie="crm_session",
                       max_age=settings.session_max_age, same_site="lax", https_only=settings.secure_cookies)

    dist = settings.frontend_dist

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        """Собранная админка (React PWA). Неизвестные пути отдают index.html."""
        if full_path.startswith(("api/", "webhook/", "media/")):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
            headers = {"Cache-Control": "public, max-age=31536000, immutable"} if full_path.startswith("assets/") \
                else {"Cache-Control": "no-cache"}
            return FileResponse(candidate, headers=headers)
        index = dist / "index.html"
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse({"detail": "admin UI is not built"}, status_code=404)

    return app


app = create_app()
