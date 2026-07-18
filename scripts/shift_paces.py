"""Decale toutes les allures cibles des seances d'UN athlete de +N secondes/km.

Usage :
    .venv/Scripts/python.exe -m scripts.shift_paces --email x@y.fr --shift -20
    .venv/Scripts/python.exe -m scripts.shift_paces --email x@y.fr --shift -20 --dry-run

Convention :
- target_low (m/s) = pace lente (lent en min/km)
- target_high (m/s) = pace rapide (rapide en min/km)
- shift +N sec/km = ralentir (allure plus grande, ex 5:34 -> 6:04) = m/s plus petit
- shift -N sec/km = accelerer (allure plus petite, ex 6:04 -> 5:44) = m/s plus grand
- new_m_per_s = 1000 / (1000/m_per_s + N)

--email est obligatoire : evite d'ecraser par erreur les allures d'un autre
compte (comptes de test, anciens athletes, etc.) present dans la meme base.
"""

from __future__ import annotations

import argparse
import sys

from sqlmodel import Session as DBSession
from sqlmodel import select

from app.db import engine
from app.models import Session, Step, TargetType, User
from app.services.workout_builder import (
    pace_min_per_km_to_m_per_s,
    pace_str_to_min_per_km,
)


def shift_m_per_s(value: float, sec_per_km: float) -> float:
    """Ajoute sec_per_km secondes par km a une vitesse en m/s. Retourne le nouveau m/s."""
    if value <= 0:
        return value
    seconds_per_km = 1000.0 / value
    new_seconds = seconds_per_km + sec_per_km
    if new_seconds <= 0:
        return value
    return 1000.0 / new_seconds


def format_pace(m_per_s: float | None) -> str:
    if not m_per_s or m_per_s <= 0:
        return "-"
    sec = 1000.0 / m_per_s
    m = int(sec // 60)
    s = int(round(sec - m * 60))
    if s == 60:
        m += 1
        s = 0
    return f"{m}:{s:02d}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", type=str, required=True,
                        help="Email de l'athlete cible (obligatoire)")
    parser.add_argument("--shift", type=float, required=True,
                        help="Decalage en secondes par kilometre (positif = plus lent, negatif = plus rapide)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Affiche les changements sans les sauver")
    args = parser.parse_args()

    shift = args.shift

    with DBSession(engine) as db:
        athlete = db.exec(select(User).where(User.email == args.email)).first()
        if athlete is None:
            sys.exit(f"Athlete introuvable : {args.email}")

        print(f"=== Decalage des allures de {shift:+g} sec/km pour {athlete.name} ({athlete.email}) ===\n")

        sessions = db.exec(
            select(Session).where(Session.athlete_id == athlete.id).order_by(Session.date)
        ).all()
        n_steps = 0
        for sess in sessions:
            print(f"--- {sess.date} {sess.name} ({len(sess.steps)} steps)")
            for step in sess.steps:
                if step.target_type != TargetType.pace:
                    continue
                if step.target_low is None and step.target_high is None:
                    continue
                old_low = step.target_low
                old_high = step.target_high
                new_low = shift_m_per_s(old_low, shift) if old_low else old_low
                new_high = shift_m_per_s(old_high, shift) if old_high else old_high
                # Garmin attend low <= high (en m/s : lent = petit)
                if new_low and new_high and new_low > new_high:
                    new_low, new_high = new_high, new_low
                print(
                    f"   step #{step.order} {step.label or step.type.value:12s}: "
                    f"{format_pace(old_high)}-{format_pace(old_low)}/km "
                    f"-> {format_pace(new_high)}-{format_pace(new_low)}/km"
                )
                step.target_low = new_low
                step.target_high = new_high
                if not args.dry_run:
                    db.add(step)
                n_steps += 1

        if args.dry_run:
            print(f"\nDRY RUN : {n_steps} steps auraient ete modifies. Aucune sauvegarde.")
            db.rollback()
        else:
            db.commit()
            print(f"\n{n_steps} steps modifies pour {len(sessions)} seances.")


if __name__ == "__main__":
    main()
