"""Suppression de séances et de leurs étapes répétées."""
from __future__ import annotations

from collections.abc import Iterable

from sqlmodel import Session as DBSession, select

from app.models import Session as TrainingSession, Step


def delete_sessions_safely(db: DBSession, sessions: Iterable[TrainingSession]) -> None:
    """Supprime des séances en cassant d'abord les références parent_repeat_id.

    PostgreSQL refuse de supprimer une étape parent tant que ses étapes enfants
    la référencent. Les relations ORM de Step ne suffisent pas à garantir un
    ordre de DELETE compatible avec cette contrainte auto-référente.
    """
    sessions = [session for session in sessions if session.id is not None]
    if not sessions:
        return

    session_ids = [session.id for session in sessions]
    steps = db.exec(select(Step).where(Step.session_id.in_(session_ids))).all()
    step_ids = [step.id for step in steps if step.id is not None]
    dependent_steps = (
        db.exec(select(Step).where(Step.parent_repeat_id.in_(step_ids))).all()
        if step_ids
        else []
    )
    for step in [*steps, *dependent_steps]:
        if step.parent_repeat_id is not None:
            step.parent_repeat_id = None
            db.add(step)
    db.flush()

    for session in sessions:
        db.delete(session)
    db.flush()
