"""Suppression de séances et de leurs étapes répétées."""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete as sql_delete, update as sql_update
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
    step_ids = list(db.exec(select(Step.id).where(Step.session_id.in_(session_ids))).all())
    if step_ids:
        # Break the self-referencing FK in one UPDATE before deleting the
        # steps in one DELETE. ORM cascades can schedule parent rows first and
        # are both slower and unsafe for large generated plans.
        db.exec(
            sql_update(Step)
            .where(Step.parent_repeat_id.in_(step_ids))
            .values(parent_repeat_id=None)
        )
        db.exec(sql_delete(Step).where(Step.session_id.in_(session_ids)))
    db.exec(sql_delete(TrainingSession).where(TrainingSession.id.in_(session_ids)))
    db.flush()
