"""Validation and application of deterministic Mi Coach program imports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session as DBSession
from sqlmodel import select

from app.models import (
    DurationType,
    Session,
    Step,
    StepType,
    TargetType,
    TrainingProfile,
    User,
)
from app.services.program_markdown_parser import ProgramMarkdownError, parse_program_markdown
from app.services.session_cleanup import delete_sessions_safely
from app.services.workout_builder import estimate_duration_and_distance, pace_min_per_km_to_m_per_s
from app.services.workout_text_parser import ParsedWorkout, WorkoutTextError, parse_workout_text

MAX_PROGRAM_CHARS = 20_000


class ProgramImportError(ValueError):
    """Erreur de validation ou d'application d'un programme importé."""


@dataclass
class ProgramImportPlan:
    markdown: str
    normalized_text: str
    start_date: date
    workouts: list[ParsedWorkout]
    existing_weeks: dict[int, list[Session]]

    @property
    def week_numbers(self) -> list[int]:
        return sorted({workout.week_number for workout in self.workouts})

    @property
    def protected_sessions(self) -> list[Session]:
        today = date.today()
        return [
            session
            for sessions in self.existing_weeks.values()
            for session in sessions
            if session.date < today or session.validated_at is not None or session.feedback is not None
        ]


def _json_dict(raw: str | None) -> dict[str, int]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(key): int(number) for key, number in value.items() if isinstance(number, (int, float))}


def _pace_data(profile: TrainingProfile) -> tuple[dict[str, int], tuple[int, int] | None]:
    targets = _json_dict(profile.pace_targets_json)
    ef_range = None
    if profile.ef_fast_sec_per_km and profile.ef_slow_sec_per_km:
        ef_range = (profile.ef_fast_sec_per_km, profile.ef_slow_sec_per_km)
    return targets, ef_range


def existing_sessions_by_import_week(
    db: DBSession,
    user: User,
    start_date: date,
    week_numbers: set[int],
) -> dict[int, list[Session]]:
    existing: dict[int, list[Session]] = {}
    for week_number in sorted(week_numbers):
        week_start = start_date + timedelta(weeks=week_number - 1)
        week_end = week_start + timedelta(days=6)
        sessions = db.exec(
            select(Session)
            .where(Session.athlete_id == user.id)
            .where(Session.date >= week_start)
            .where(Session.date <= week_end)
            .order_by(Session.date, Session.id)
        ).all()
        if sessions:
            existing[week_number] = sessions
    return existing


def _parse_normalized_text(
    normalized_text: str,
    start_date: date,
    profile: TrainingProfile,
) -> list[ParsedWorkout]:
    targets, ef_range = _pace_data(profile)
    try:
        parsed = parse_workout_text(
            normalized_text,
            start_date=start_date,
            pace_targets=targets,
            ef_range=ef_range,
            sessions_per_week=profile.sessions_per_week,
        )
    except WorkoutTextError as exc:
        raise ProgramImportError(str(exc)) from exc
    dates = [workout.date for workout in parsed.workouts]
    if len(set(dates)) != len(dates):
        raise ProgramImportError("Plusieurs séances sont prévues le même jour.")
    if not parsed.workouts:
        raise ProgramImportError("Le programme ne contient aucune séance.")
    return parsed.workouts


def build_program_import_plan(
    db: DBSession,
    user: User,
    profile: TrainingProfile,
    markdown: str,
    *,
    overwrite_existing: bool = False,
) -> ProgramImportPlan:
    if len(markdown) > MAX_PROGRAM_CHARS:
        raise ProgramImportError(f"Le programme dépasse la limite de {MAX_PROGRAM_CHARS} caractères.")
    try:
        parsed_file = parse_program_markdown(markdown)
    except ProgramMarkdownError as exc:
        raise ProgramImportError(str(exc)) from exc
    workouts = _parse_normalized_text(parsed_file.normalized_text, parsed_file.start_date, profile)
    existing = existing_sessions_by_import_week(
        db, user, parsed_file.start_date, {workout.week_number for workout in workouts}
    )
    plan = ProgramImportPlan(
        markdown=markdown,
        normalized_text=parsed_file.normalized_text,
        start_date=parsed_file.start_date,
        workouts=workouts,
        existing_weeks=existing,
    )
    return plan


def preview_rows(plan: ProgramImportPlan) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for workout in plan.workouts:
        rows.append(
            {
                "week_number": workout.week_number,
                "session_number": workout.session_number or workout.weekday + 1,
                "date": workout.date,
                "name": workout.name,
                "steps": [
                    {
                        "kind": step.kind,
                        "type": step.step_type,
                        "label": step.label,
                        "duration_type": step.duration_type,
                        "duration_value": step.duration_value,
                        "target_type": step.target_type,
                        "pace_fast_seconds_per_km": step.pace_fast_seconds_per_km,
                        "pace_slow_seconds_per_km": step.pace_slow_seconds_per_km,
                        "repeat_count": step.repeat_count,
                    }
                    for step in workout.steps
                ],
            }
        )
    return rows


def _create_imported_sessions(
    db: DBSession,
    user: User,
    profile: TrainingProfile,
    plan: ProgramImportPlan,
) -> list[Session]:
    created_sessions: list[Session] = []
    for workout in plan.workouts:
        session_number = workout.session_number or workout.weekday + 1
        session = Session(
            athlete_id=user.id,
            date=workout.date,
            name=f"Semaine {workout.week_number} · Séance {session_number} · {workout.name}"[:100],
            description=f"Créée depuis un programme texte, semaine {workout.week_number}.",
            difficulty=2,
        )
        db.add(session)
        db.flush()
        repeat_ids: dict[int, int] = {}
        created_steps: list[Step] = []
        for index, parsed_step in enumerate(workout.steps):
            step = Step(
                session_id=session.id,
                order=index + 1,
                kind=parsed_step.kind,
                repeat_count=parsed_step.repeat_count,
                type=StepType(parsed_step.step_type),
                label=parsed_step.label or None,
                duration_type=DurationType(parsed_step.duration_type),
                duration_value=parsed_step.duration_value or None,
                target_type=TargetType(parsed_step.target_type),
            )
            if parsed_step.target_type == "pace":
                fast = parsed_step.pace_fast_seconds_per_km
                slow = parsed_step.pace_slow_seconds_per_km or fast
                if fast is not None and slow is not None:
                    step.target_low = pace_min_per_km_to_m_per_s(slow / 60)
                    step.target_high = pace_min_per_km_to_m_per_s(fast / 60)
            if parsed_step.parent_repeat_index is not None:
                step.parent_repeat_id = repeat_ids.get(parsed_step.parent_repeat_index)
            db.add(step)
            db.flush()
            created_steps.append(step)
            if parsed_step.kind == "repeat":
                repeat_ids[index] = step.id
        session.estimated_duration_min, session.estimated_distance_km = estimate_duration_and_distance(created_steps)
        db.add(session)
        created_sessions.append(session)

    if profile.goal == "other" and profile.race_date:
        race_week = ((profile.race_date - plan.start_date).days // 7) + 1
        has_race = any(
            workout.week_number == race_week
            and workout.date == profile.race_date
            and "course" in workout.name.lower()
            for workout in plan.workouts
        )
        if not has_race:
            race = Session(
                athlete_id=user.id,
                date=profile.race_date,
                name="Course",
                description=f"Jour de course, semaine {race_week}.",
                difficulty=4,
            )
            db.add(race)
            created_sessions.append(race)
    return created_sessions


def apply_program_import(
    db: DBSession,
    user: User,
    profile: TrainingProfile,
    plan: ProgramImportPlan,
    *,
    allow_existing: bool = False,
) -> list[Session]:
    if plan.existing_weeks and not allow_existing:
        week_labels = ", ".join(str(week) for week in sorted(plan.existing_weeks))
        raise ProgramImportError(
            f"Des séances existent déjà dans la ou les semaines {week_labels}. "
            "Une confirmation explicite est nécessaire pour les remplacer."
        )
    protected_dates = {session.date for session in plan.protected_sessions}
    imported_dates = {workout.date for workout in plan.workouts}
    if protected_dates & imported_dates:
        raise ProgramImportError(
            "Le programme touche une séance passée ou validée. Ces séances doivent rester protégées."
        )

    replaceable = [
        session
        for sessions in plan.existing_weeks.values()
        for session in sessions
        if session.date >= date.today() and session.validated_at is None and session.feedback is None
    ]
    if replaceable:
        delete_sessions_safely(db, replaceable)
    created = _create_imported_sessions(db, user, profile, plan)
    db.commit()
    return created
