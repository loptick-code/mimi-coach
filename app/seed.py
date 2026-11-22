"""Seed initial des comptes configurés, avec les mêmes droits pour tous."""

from __future__ import annotations

from sqlalchemy import func
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
    """Crée l'utilisateur s'il manque et normalise son rôle historique."""
    email = email.strip().lower()
    existing = db.exec(select(User).where(func.lower(User.email) == email)).first()
    if existing is not None:
        changed = False
        if existing.email != email:
            existing.email = email
            changed = True
        if existing.role != role:
            existing.role = role
            changed = True
        if changed:
            db.add(existing)
            db.commit()
            print(f"[seed] Role normalise={email}")
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
    common_role = UserRole.athlete
    with DBSession(engine) as db:
        if _ensure_user(
            db,
            email=settings.coach_email,
            name=settings.coach_name,
            password=settings.coach_password,
            role=common_role,
        ):
            print(f"[seed] Cree compte={settings.coach_email}")

        if _ensure_user(
            db,
            email=settings.athlete_email,
            name=settings.athlete_name,
            password=settings.athlete_password,
            role=common_role,
        ):
            print(f"[seed] Cree compte={settings.athlete_email}")

        # Compte supplémentaire optionnel, créé seulement si email et mot de passe sont définis.
        if settings.athlete2_email and settings.athlete2_password:
            if _ensure_user(
                db,
                email=settings.athlete2_email,
                name=settings.athlete2_name,
                password=settings.athlete2_password,
                role=common_role,
            ):
                print(f"[seed] Cree compte={settings.athlete2_email}")
