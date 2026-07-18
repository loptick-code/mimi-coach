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
    if settings.athlete2_email and settings.athlete2_password in ("", "changeme", "changeme123"):
        issues.append("ATHLETE2_PASSWORD est trop faible (defini car ATHLETE2_EMAIL est set)")
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

    @app.exception_handler(Exception)
    async def handle_generic_exception(request: Request, exc: Exception):
        """Filet pour 500 : affiche un message lisible avec le type d'erreur.

        En prod (debug=False) on cache le traceback ; en dev on l'affiche.
        """
        import traceback
        from fastapi.responses import HTMLResponse

        accept = request.headers.get("accept", "")
        is_html = "text/html" in accept or accept == "" or accept == "*/*"

        if not is_html:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "internal_server_error"}, status_code=500)

        title = "Oups, ça a planté"
        msg = f"{type(exc).__name__}: {exc}"
        tb_html = ""
        if settings.debug:
            tb = traceback.format_exc()
            tb_html = (
                f'<pre style="background:#f5f5f4;padding:1rem;border-radius:.5rem;'
                f'overflow-x:auto;font-size:.75rem;color:#44403c;'
                f'white-space:pre-wrap;word-break:break-word;">{tb}</pre>'
            )

        html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Erreur</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{font-family:ui-sans-serif,system-ui,sans-serif;background:#fafaf9;color:#1c1917;
margin:0;padding:2rem;display:flex;justify-content:center;}}
.card{{max-width:640px;width:100%;background:#fff;border:1px solid #e7e5e4;
border-radius:1rem;padding:2rem;box-shadow:0 1px 3px rgba(0,0,0,0.05);}}
h1{{margin:0 0 .5rem;font-size:1.5rem;}}
.msg{{background:#fef2f2;border:1px solid #fecaca;color:#991b1b;
padding:.75rem 1rem;border-radius:.5rem;font-family:ui-monospace,monospace;
font-size:.85rem;margin:1rem 0;word-break:break-word;}}
a{{color:#0c4a6e;text-decoration:underline;}}
</style></head><body>
<div class="card">
<h1>{title}</h1>
<p style="color:#57534e;">Une erreur inattendue s'est produite côté serveur.</p>
<div class="msg">{msg}</div>
{tb_html}
<p><a href="/">← Retour à l'accueil</a></p>
</div></body></html>"""
        return HTMLResponse(html, status_code=500)

    return app


app = create_app()
