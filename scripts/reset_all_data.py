"""Réinitialise explicitement les données Mi Coach et recrée les comptes configurés.

Sans argument, le script affiche un aperçu sans rien effacer. La phrase de
confirmation exacte est nécessaire pour exécuter la suppression.
"""

from __future__ import annotations

import argparse

from sqlalchemy import func
from sqlmodel import Session as DBSession
from sqlmodel import select

from app.auth import hash_password
from app.config import get_settings
from app.db import engine
from app.models import Session as WorkoutSession
from app.models import Step, User, UserRole


CONFIRMATION_PHRASE = "RESET-MI-COACH"
DEFAULT_PASSWORDS = {"changeme", "password", "123456"}


def _configured_accounts() -> list[User]:
    """Build replacement accounts before touching the database."""
    settings = get_settings()
    specs = [
        (settings.coach_email, settings.coach_name, settings.coach_password),
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

    seen_emails: set[str] = set()
    new_users: list[User] = []
    for email, name, password in specs:
        normalized_email = email.strip().lower()
        if not normalized_email or not password:
            raise ValueError("Chaque compte configuré doit avoir un email et un mot de passe.")
        if normalized_email in seen_emails:
            raise ValueError("Les emails des comptes configurés doivent être uniques.")
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
                role=UserRole.athlete,
            )
        )
    return new_users


def _count_rows(db: DBSession, model: type) -> int:
    return int(db.exec(select(func.count()).select_from(model)).one())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Supprime les données Mi Coach et recrée les comptes configurés."
    )
    parser.add_argument(
        "--confirm-reset",
        metavar="PHRASE",
        help=f"Exécute le reset uniquement avec la valeur {CONFIRMATION_PHRASE}.",
    )
    args = parser.parse_args()

    with DBSession(engine) as db:
        counts = {
            "comptes": _count_rows(db, User),
            "séances": _count_rows(db, WorkoutSession),
            "étapes": _count_rows(db, Step),
        }

    print(f"Type de base : {engine.url.get_backend_name()}")
    print(
        "Données actuelles : "
        + ", ".join(f"{count} {label}" for label, count in counts.items())
    )

    if args.confirm_reset != CONFIRMATION_PHRASE:
        if args.confirm_reset:
            parser.error(f"La phrase de confirmation doit être exactement {CONFIRMATION_PHRASE}.")
        print("Aperçu seulement. Aucune donnée n'a été modifiée.")
        print(
            f"Pour supprimer définitivement les comptes, séances, étapes, ressentis et connexions Garmin, "
            f"relance avec --confirm-reset {CONFIRMATION_PHRASE}."
        )
        return 0

    if engine.url.get_backend_name() != "postgresql":
        parser.error(
            "Reset refusé : la base n'est pas PostgreSQL. Vérifie que DATABASE_URL pointe vers Supabase."
        )

    try:
        replacement_users = _configured_accounts()
    except ValueError as exc:
        parser.error(str(exc))

    # Une transaction unique : en cas d'échec, les anciennes données restent.
    with DBSession(engine) as db:
        existing_users = db.exec(select(User)).all()
        for user in existing_users:
            db.delete(user)
        db.flush()
        db.add_all(replacement_users)
        db.commit()

    print(
        f"Reset terminé. {len(replacement_users)} comptes ont été recréés avec les mêmes droits."
    )
    print("Chaque personne devra reconnecter Garmin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
