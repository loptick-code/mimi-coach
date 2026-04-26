"""Routes /login et /logout."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session as DBSession
from sqlmodel import select

from app.auth import login_user, logout_user, verify_password
from app.db import get_session
from app.models import User
from app.templates import templates

router = APIRouter()


@router.get("/login")
async def login_page(request: Request, error: str | None = None):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": error},
    )


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: DBSession = Depends(get_session),
):
    user = db.exec(select(User).where(User.email == email)).first()
    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Email ou mot de passe invalide"},
            status_code=400,
        )
    login_user(request, user)
    return RedirectResponse(url="/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    logout_user(request)
    return RedirectResponse(url="/login", status_code=303)
