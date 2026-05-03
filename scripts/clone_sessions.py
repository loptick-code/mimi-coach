"""Clone toutes les seances d'un athlete source vers un athlete cible.

Usage :
    python -m scripts.clone_sessions <source_email> <target_email>

Le clone copie le programme (Session + Steps avec leurs blocs repeat) en
remettant a zero tous les champs lies a l'execution :
- garmin_workout_id, pushed_at, last_push_error
- feedback, validated_at, pace_adjustment_at_validation
- garmin_activity_id, actual_duration_sec, actual_distance_m

La date des seances est conservee a l'identique. Si une seance avec la meme
date+nom existe deja chez la cible, elle est ignoree (le script est donc
re-executable sans creer de doublons).
"""

from __future__ import annotations

import sys

from sqlmodel import Session as DBSession
from sqlmodel import select

from app.db import engine, init_db
from app.models import Session, Step, User


def clone_sessions(source_email: str, target_email: str) -> None:
    init_db()
    with DBSession(engine) as db:
        source = db.exec(select(User).where(User.email == source_email)).first()
        target = db.exec(select(User).where(User.email == target_email)).first()
        if source is None:
            sys.exit(f"ERREUR : athlete source introuvable ({source_email})")
        if target is None:
            sys.exit(f"ERREUR : athlete cible introuvable ({target_email})")
        if source.id == target.id:
            sys.exit("ERREUR : source et cible sont le meme utilisateur")

        source_sessions = db.exec(
            select(Session).where(Session.athlete_id == source.id)
        ).all()
        existing_targets = db.exec(
            select(Session.date, Session.name).where(Session.athlete_id == target.id)
        ).all()
        existing_keys = {(d, n) for d, n in existing_targets}

        created = 0
        skipped = 0
        for src in source_sessions:
            if (src.date, src.name) in existing_keys:
                skipped += 1
                continue

            new_session = Session(
                athlete_id=target.id,
                date=src.date,
                name=src.name,
                description=src.description,
                difficulty=src.difficulty,
                estimated_duration_min=src.estimated_duration_min,
                estimated_distance_km=src.estimated_distance_km,
                # Champs d'execution remis a zero pour la cible
                garmin_workout_id=None,
                pushed_at=None,
                last_push_error=None,
                feedback=None,
                validated_at=None,
                pace_adjustment_at_validation=None,
                garmin_activity_id=None,
                actual_duration_sec=None,
                actual_distance_m=None,
            )
            db.add(new_session)
            db.flush()  # genere new_session.id

            # Premiere passe : cree tous les nouveaux steps avec parent_repeat_id=None
            # et construit le mapping old_step_id -> new_step_id
            src_steps = sorted(src.steps, key=lambda s: s.order)
            id_map: dict[int, int] = {}
            new_steps_with_parent: list[tuple[Step, int]] = []  # (new_step, old_parent_id)

            for old_step in src_steps:
                new_step = Step(
                    session_id=new_session.id,
                    order=old_step.order,
                    parent_repeat_id=None,
                    kind=old_step.kind,
                    repeat_count=old_step.repeat_count,
                    type=old_step.type,
                    label=old_step.label,
                    duration_type=old_step.duration_type,
                    duration_value=old_step.duration_value,
                    target_type=old_step.target_type,
                    target_low=old_step.target_low,
                    target_high=old_step.target_high,
                    notes=old_step.notes,
                )
                db.add(new_step)
                db.flush()  # genere new_step.id
                id_map[old_step.id] = new_step.id
                if old_step.parent_repeat_id is not None:
                    new_steps_with_parent.append((new_step, old_step.parent_repeat_id))

            # Seconde passe : recable les parent_repeat_id via le mapping
            for new_step, old_parent_id in new_steps_with_parent:
                new_step.parent_repeat_id = id_map[old_parent_id]
                db.add(new_step)

            created += 1

        db.commit()

    print(
        f"OK : {created} seance(s) clonee(s) de {source_email} vers {target_email}"
        + (f", {skipped} ignoree(s) (deja presente(s))" if skipped else "")
    )


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("Usage : python -m scripts.clone_sessions <source_email> <target_email>")
    clone_sessions(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
