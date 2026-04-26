"""Seed initial : cree les 2 comptes (coach + athlete) au premier demarrage."""

from __future__ import annotations

from sqlmodel import Session as DBSession
from sqlmodel import select

from app.auth import hash_password
from app.config import get_settings
from app.db import engine
from app.models import User, UserRole


def seed_users() -> None:
    settings = get_settings()
    with DBSession(engine) as db:
        existing = db.exec(select(User)).first()
        if existing is not None:
            return

        coach = User(
            email=settings.coach_email,
            name="Coach",
            password_hash=hash_password(settings.coach_password),
            role=UserRole.coach,
        )
        athlete = User(
            email=settings.athlete_email,
            name=settings.athlete_name,
            password_hash=hash_password(settings.athlete_password),
            role=UserRole.athlete,
        )
        db.add(coach)
        db.add(athlete)
        db.commit()
        print(f"[seed] Cree coach={coach.email} et athlete={athlete.email}")
