"""Supprime et recrée uniquement les comptes athlètes Mi Coach.

Le compte identifié par COACH_EMAIL et toutes ses données sont conservés.
Sans argument de confirmation, le script affiche uniquement un aperçu.
"""

from __future__ import annotations

import argparse

from sqlalchemy import delete as sql_delete, func
from sqlmodel import Session as DBSession
from sqlmodel import select

from app.auth import hash_password
from app.config import get_settings
from app.db import engine
from app.models import (
    MCPAuthorizationCode,
    MCPChangeProposal,
    MCPToken,
    Session as WorkoutSession,
    TrainingPlanState,
    TrainingProfile,
    User,
)
from app.services.session_cleanup import delete_sessions_safely


CONFIRMATION_PHRASE = "RESET-MI-COACH-ATHLETES"
DEFAULT_PASSWORDS = {"changeme", "password", "123456"}


def _normalized_email(value: str) -> str:
    return value.strip().lower()


def _configured_athletes() -> list[User]:
    """Build replacement athlete accounts before touching the database."""
    settings = get_settings()
    specs = [
        (settings.athlete_email, settings.athlete_name, settings.athlete_password),
    ]

    if settings.athlete2_email or settings.athlete2_password:
        if not settings.athlete2_email or not settings.athlete2_password:
            raise ValueError(
                "Renseigne ATHLETE2_EMAIL et ATHLETE2_PASSWORD, ou laisse les deux vides."
            )
        specs.append(
            (settings.athlete2_email, settings.athlete2_name, settings.athlete2_password)
        )

    coach_email = _normalized_email(settings.coach_email)
    seen_emails: set[str] = set()
    new_users: list[User] = []
    for email, name, password in specs:
        normalized_email = _normalized_email(email)
        if not normalized_email or not password:
            raise ValueError("Chaque compte athlète configuré doit avoir un email et un mot de passe.")
        if normalized_email == coach_email:
            raise ValueError("Un nouvel email athlète ne peut pas être celui du compte coach.")
        if normalized_email in seen_emails:
            raise ValueError("Les emails des comptes athlètes doivent être uniques.")
        if normalized_email.endswith("@example.com"):
            raise ValueError("Remplace les emails example.com avant le reset.")
        if password.lower() in DEFAULT_PASSWORDS:
            raise ValueError("Remplace les mots de passe par défaut avant le reset.")
        seen_emails.add(normalized_email)
        new_users.append(
            User(
                email=normalized_email,
                name=name.strip() or normalized_email.split("@", 1)[0],
                password_hash=hash_password(password),
            )
        )
    return new_users


def _count_rows(db: DBSession, model: type, user_ids: list[int]) -> int:
    if not user_ids:
        return 0
    field = (
        getattr(model, "user_id")
        if hasattr(model, "user_id")
        else getattr(model, "athlete_id")
    )
    return int(
        db.exec(select(func.count()).select_from(model).where(field.in_(user_ids))).one()
    )


def _load_athlete_users(db: DBSession, coach_email: str) -> tuple[User, list[User]]:
    users = db.exec(select(User)).all()
    coaches = [user for user in users if _normalized_email(user.email) == coach_email]
    if len(coaches) != 1:
        raise ValueError(
            f"Reset refusé : le compte coach {coach_email} doit exister une seule fois "
            f"(trouvé : {len(coaches)})."
        )
    coach = coaches[0]
    athletes = [user for user in users if user.id != coach.id]
    return coach, athletes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Supprime et recrée uniquement les comptes athlètes, en conservant le coach."
    )
    parser.add_argument(
        "--confirm-reset",
        metavar="PHRASE",
        help=f"Exécute le reset uniquement avec la valeur {CONFIRMATION_PHRASE}.",
    )
    args = parser.parse_args()

    settings = get_settings()
    coach_email = _normalized_email(settings.coach_email)

    try:
        replacement_users = _configured_athletes()
    except ValueError as exc:
        parser.error(str(exc))

    with DBSession(engine) as db:
        try:
            coach, athletes = _load_athlete_users(db, coach_email)
        except ValueError as exc:
            parser.error(str(exc))

        athlete_ids = [user.id for user in athletes if user.id is not None]
        counts = {
            "athlètes supprimés": len(athletes),
            "séances athlètes": _count_rows(db, WorkoutSession, athlete_ids),
            "profils": _count_rows(db, TrainingProfile, athlete_ids),
            "états de plan": _count_rows(db, TrainingPlanState, athlete_ids),
            "tokens MCP": _count_rows(db, MCPToken, athlete_ids),
        }

    print(f"Type de base : {engine.url.get_backend_name()}")
    print(f"Compte coach conservé : {coach.email}")
    print(
        "Données athlètes ciblées : "
        + ", ".join(f"{count} {label}" for label, count in counts.items())
    )
    print(
        "Nouveaux comptes athlètes : "
        + ", ".join(user.email for user in replacement_users)
    )

    if args.confirm_reset != CONFIRMATION_PHRASE:
        if args.confirm_reset:
            parser.error(f"La phrase de confirmation doit être exactement {CONFIRMATION_PHRASE}.")
        print("Aperçu seulement. Aucune donnée n'a été modifiée.")
        print(
            "Pour supprimer et recréer uniquement les athlètes, relance avec "
            f"--confirm-reset {CONFIRMATION_PHRASE}."
        )
        return 0

    if engine.url.get_backend_name() != "postgresql":
        parser.error(
            "Reset refusé : la base n'est pas PostgreSQL. Vérifie que DATABASE_URL pointe vers Supabase."
        )

    with DBSession(engine) as db:
        coach, athletes = _load_athlete_users(db, coach_email)
        athlete_ids = [user.id for user in athletes if user.id is not None]

        sessions = db.exec(
            select(WorkoutSession).where(WorkoutSession.athlete_id.in_(athlete_ids))
        ).all()
        delete_sessions_safely(db, sessions)

        if athlete_ids:
            for model in (
                MCPAuthorizationCode,
                MCPChangeProposal,
                MCPToken,
                TrainingPlanState,
                TrainingProfile,
            ):
                db.exec(sql_delete(model).where(model.user_id.in_(athlete_ids)))
            db.exec(sql_delete(User).where(User.id.in_(athlete_ids)))

        db.add_all(replacement_users)
        db.commit()

    print(
        f"Reset terminé. Le compte coach {coach.email} a été conservé. "
        f"{len(replacement_users)} comptes athlètes ont été recréés."
    )
    print("Les athlètes devront reconnecter Garmin et refaire leur onboarding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
