"""Seed initial : cree les comptes (coach + athletes) idempotemment a chaque demarrage."""

from __future__ import annotations

from sqlmodel import Session as DBSession
from sqlmodel import select

from app.auth import hash_password
from app.config import get_settings
from app.db import engine
from app.models import User, UserRole


def _ensure_user(
    db: DBSession,
    *,
    email: str,
    name: str,
    password: str,
    role: UserRole,
) -> bool:
    """Cree l'utilisateur s'il n'existe pas. Retourne True si cree."""
    existing = db.exec(select(User).where(User.email == email)).first()
    if existing is not None:
        return False
    db.add(
        User(
            email=email,
            name=name,
            password_hash=hash_password(password),
            role=role,
        )
    )
    db.commit()
    return True


def seed_users() -> None:
    settings = get_settings()
    with DBSession(engine) as db:
        if _ensure_user(
            db,
            email=settings.coach_email,
            name="Coach",
            password=settings.coach_password,
            role=UserRole.coach,
        ):
            print(f"[seed] Cree coach={settings.coach_email}")

        if _ensure_user(
            db,
            email=settings.athlete_email,
            name=settings.athlete_name,
            password=settings.athlete_password,
            role=UserRole.athlete,
        ):
            print(f"[seed] Cree athlete={settings.athlete_email}")

        # 2eme athlete optionnel : seede uniquement si LEA_EMAIL et LEA_PASSWORD sont definis
        if settings.lea_email and settings.lea_password:
            if _ensure_user(
                db,
                email=settings.lea_email,
                name=settings.lea_name,
                password=settings.lea_password,
                role=UserRole.athlete,
            ):
                print(f"[seed] Cree athlete={settings.lea_email}")
