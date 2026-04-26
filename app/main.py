"""Application FastAPI."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db import init_db
from app.routes import auth as auth_routes
from app.routes import garmin as garmin_routes
from app.routes import sessions as session_routes
from app.seed import seed_users
from app.templates import TEMPLATE_DIR

settings = get_settings()


_DEV_SECRET_DEFAULT = "dev-secret-change-me-in-production"


def _validate_production_config() -> None:
    """En prod (debug=false), refuse de démarrer avec des secrets par défaut."""
    if settings.debug:
        return
    issues = []
    if not settings.fernet_key:
        issues.append("FERNET_KEY non défini")
    if settings.session_secret in ("", _DEV_SECRET_DEFAULT):
        issues.append("SESSION_SECRET utilise la valeur par défaut (à régénérer)")
    if settings.coach_password in ("", "changeme", "changeme123"):
        issues.append("COACH_PASSWORD est trop faible")
    if settings.athlete_password in ("", "changeme", "changeme123"):
        issues.append("ATHLETE_PASSWORD est trop faible")
    if issues:
        raise RuntimeError(
            "Configuration de production invalide :\n  - "
            + "\n  - ".join(issues)
            + "\nDéfinis ces variables dans l'environnement avant de démarrer."
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _validate_production_config()
    init_db()
    seed_users()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Mimi Coach",
        debug=settings.debug,
        lifespan=lifespan,
    )

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=settings.https_only_cookies,
        max_age=60 * 60 * 24 * 30,  # 30 jours
    )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    static_dir = TEMPLATE_DIR.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(auth_routes.router)
    app.include_router(session_routes.router)
    app.include_router(garmin_routes.router)

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException):
        accept = request.headers.get("accept", "")
        is_html = "text/html" in accept or accept == "" or accept == "*/*"
        # 401 -> redirect vers /login
        if exc.status_code == 401:
            if is_html:
                return RedirectResponse(url="/login", status_code=303)
        # 404 -> page sympa avec ref Jul
        if exc.status_code == 404 and is_html:
            from app.templates import templates
            return templates.TemplateResponse(
                request,
                "404.html",
                {"user": None},
                status_code=404,
            )
        from fastapi.exception_handlers import http_exception_handler

        return await http_exception_handler(request, exc)

    return app


app = create_app()
