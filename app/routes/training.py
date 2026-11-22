"""Onboarding, génération de plan et import texte déterministe."""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session as DBSession, select

from app.auth import require_user
from app.db import get_session
from app.models import (
    DurationType,
    Session,
    Step,
    StepType,
    TargetType,
    TrainingPlanState,
    TrainingProfile,
    User,
)
from app.services.training_paces import (
    PACE_LABELS,
    calculate_pace_targets,
    pace_to_seconds,
    seconds_to_pace,
    supported_goal,
    validate_ef_range,
)
from app.services.session_cleanup import delete_sessions_safely
from app.services.training_plan_library import PlanStep, generate_schedule
from app.services.workout_builder import estimate_duration_and_distance, pace_min_per_km_to_m_per_s
from app.services.workout_text_parser import ParsedWorkout, WorkoutTextError, parse_workout_text
from app.templates import templates

router = APIRouter()
GOAL_LABELS = {"10k": "10 km", "half": "Semi-marathon", "marathon": "Marathon"}
PACE_KEYS = list(PACE_LABELS)


def _profile(db: DBSession, user: User) -> TrainingProfile | None:
    return db.exec(select(TrainingProfile).where(TrainingProfile.user_id == user.id)).first()


def _plan_state(db: DBSession, user: User) -> TrainingPlanState | None:
    return db.exec(select(TrainingPlanState).where(TrainingPlanState.user_id == user.id)).first()


def _json_dict(raw: str | None) -> dict[str, int]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return {str(key): int(number) for key, number in value.items() if isinstance(number, (int, float))} if isinstance(value, dict) else {}


def _json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _profile_context(profile: TrainingProfile | None) -> dict[str, Any]:
    if profile is None:
        return {
            "goal": "10k", "race_date": "", "sessions_per_week": 4,
            "ef_fast": "", "ef_slow": "", "pace_targets": {}, "manual_paces": [],
        }
    return {
        "goal": profile.goal,
        "race_date": profile.race_date.isoformat() if profile.race_date else "",
        "sessions_per_week": profile.sessions_per_week,
        "ef_fast": seconds_to_pace(profile.ef_fast_sec_per_km),
        "ef_slow": seconds_to_pace(profile.ef_slow_sec_per_km),
        "pace_targets": _json_dict(profile.pace_targets_json),
        "manual_paces": _json_list(profile.manual_paces_json),
    }


def _pace_rows(targets: dict[str, int], manual: list[str] | set[str]) -> list[dict[str, Any]]:
    return [
        {"key": key, "label": PACE_LABELS[key], "value": seconds_to_pace(targets.get(key)), "manual": key in manual}
        for key in PACE_KEYS
    ]


def _form_profile(form: Any) -> dict[str, Any]:
    goal = str(form.get("goal") or "").strip()
    if not supported_goal(goal):
        raise ValueError("Choisis un objectif parmi 10 km, semi-marathon ou marathon.")

    race_date_raw = str(form.get("race_date") or "").strip()
    if not race_date_raw:
        raise ValueError("La date de course est obligatoire.")
    try:
        race_date = date.fromisoformat(race_date_raw)
    except ValueError as exc:
        raise ValueError("La date de course est invalide.") from exc
    if race_date < date.today():
        raise ValueError("La date de course doit être aujourd'hui ou dans le futur.")

    try:
        sessions_per_week = int(form.get("sessions_per_week") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Choisis 3 ou 4 séances par semaine.") from exc
    if sessions_per_week not in (3, 4):
        raise ValueError("Choisis 3 ou 4 séances par semaine.")

    ef_fast_raw = str(form.get("ef_fast") or "").strip()
    ef_slow_raw = str(form.get("ef_slow") or "").strip()
    if not ef_fast_raw or not ef_slow_raw:
        raise ValueError("La plage EF est obligatoire. Tu peux saisir une estimation et la modifier ensuite.")
    ef_fast = pace_to_seconds(ef_fast_raw)
    ef_slow = pace_to_seconds(ef_slow_raw)
    validate_ef_range(ef_fast, ef_slow)
    return {
        "goal": goal,
        "race_date": race_date,
        "sessions_per_week": sessions_per_week,
        "ef_fast_sec_per_km": ef_fast,
        "ef_slow_sec_per_km": ef_slow,
    }


def _store_profile(db: DBSession, user: User, data: dict[str, Any], form: Any, existing: TrainingProfile | None) -> TrainingProfile:
    auto_targets = calculate_pace_targets(data["goal"], data["ef_fast_sec_per_km"], data["ef_slow_sec_per_km"])
    old_targets = _json_dict(existing.pace_targets_json) if existing else {}
    old_manual = set(_json_list(existing.manual_paces_json)) if existing else set()
    reset_all = str(form.get("reset_auto_paces") or "").lower() in ("1", "true", "on", "yes")
    new_targets: dict[str, int] = {}
    manual_paces: set[str] = set()
    for key in PACE_KEYS:
        raw_value = str(form.get(f"pace_{key}") or "").strip()
        manual_flag = str(form.get(f"manual_{key}") or "").lower() in ("1", "true", "on", "yes")
        submitted = pace_to_seconds(raw_value) if raw_value else None
        if not reset_all and manual_flag and submitted is not None:
            new_targets[key] = submitted
            manual_paces.add(key)
        elif key in auto_targets:
            new_targets[key] = auto_targets[key]
        elif not reset_all and key in old_manual and key in old_targets:
            new_targets[key] = old_targets[key]
            manual_paces.add(key)

    if existing is None:
        existing = TrainingProfile(user_id=user.id)
    existing.goal = data["goal"]
    existing.race_date = data["race_date"]
    existing.sessions_per_week = data["sessions_per_week"]
    existing.ef_fast_sec_per_km = data["ef_fast_sec_per_km"]
    existing.ef_slow_sec_per_km = data["ef_slow_sec_per_km"]
    existing.pace_targets_json = json.dumps(new_targets, ensure_ascii=False)
    existing.manual_paces_json = json.dumps(sorted(manual_paces), ensure_ascii=False)
    db.add(existing)
    db.commit()
    db.refresh(existing)
    return existing


def _delete_generated_future_sessions(db: DBSession, user: User, state: TrainingPlanState | None) -> None:
    today = date.today()
    sessions_by_id: dict[int, Session] = {}
    if state is not None:
        for raw_id in _json_list(state.generated_session_ids_json):
            session = db.get(Session, int(raw_id))
            if session and session.athlete_id == user.id and session.date >= today and session.validated_at is None:
                sessions_by_id[session.id] = session

    # Fallback for plans created before TrainingPlanState was populated, or
    # after a generation failed halfway through. Generated sessions are
    # identifiable by their Plan ... description, while manual sessions use
    # a different description and are preserved.
    orphaned_generated = db.exec(
        select(Session)
        .where(Session.athlete_id == user.id)
        .where(Session.date >= today)
        .where(Session.validated_at.is_(None))
        .where(Session.description.like("Plan %"))
    ).all()
    for session in orphaned_generated:
        sessions_by_id[session.id] = session

    sessions_to_delete = list(sessions_by_id.values())
    delete_sessions_safely(db, sessions_to_delete)


def _create_plan_steps(db: DBSession, session_id: int, plan_steps: list[PlanStep]) -> list[Step]:
    created: list[Step] = []
    order = 0

    def add(step_spec: PlanStep, parent_id: int | None = None) -> None:
        nonlocal order
        order += 1
        is_repeat = step_spec.repeat_count is not None and bool(step_spec.children)
        db_step = Step(
            session_id=session_id,
            order=order,
            parent_repeat_id=parent_id,
            kind="repeat" if is_repeat else "step",
            repeat_count=step_spec.repeat_count if is_repeat else None,
            type=StepType(step_spec.step_type if step_spec.step_type in {item.value for item in StepType} else StepType.other.value),
            label=step_spec.label[:120] if step_spec.label else None,
            duration_type=DurationType.distance if step_spec.distance_m is not None else DurationType.time,
            duration_value=step_spec.distance_m if step_spec.distance_m is not None else step_spec.duration_seconds,
            target_type=TargetType.no_target,
        )
        if not is_repeat and step_spec.source_pace_fast is not None:
            slow = step_spec.source_pace_slow or step_spec.source_pace_fast
            fast = step_spec.source_pace_fast
            db_step.target_type = TargetType.pace
            db_step.target_low = pace_min_per_km_to_m_per_s(slow / 60)
            db_step.target_high = pace_min_per_km_to_m_per_s(fast / 60)
        db.add(db_step)
        db.flush()
        created.append(db_step)
        if is_repeat:
            for child in step_spec.children:
                add(child, db_step.id)

    for item in plan_steps:
        add(item)
    return created


def _apply_manual_paces(plan_steps: list[PlanStep], manual_targets: dict[str, int]) -> None:
    for step in plan_steps:
        if step.target_key in manual_targets:
            step.source_pace_fast = manual_targets[step.target_key]
            step.source_pace_slow = manual_targets[step.target_key]
        _apply_manual_paces(step.children, manual_targets)


def _generate_plan(db: DBSession, user: User, profile: TrainingProfile) -> TrainingPlanState:
    _delete_generated_future_sessions(db, user, _plan_state(db, user))
    _, schedule = generate_schedule(
        profile.goal,
        profile.race_date,
        profile.ef_fast_sec_per_km,
        profile.ef_slow_sec_per_km,
        profile.sessions_per_week,
    )
    manual_keys = set(_json_list(profile.manual_paces_json))
    stored_targets = _json_dict(profile.pace_targets_json)
    manual_targets = {key: stored_targets[key] for key in manual_keys if key in stored_targets}
    generated_ids: list[str] = []
    for planned in schedule:
        _apply_manual_paces(planned.steps, manual_targets)
        session = Session(
            athlete_id=user.id,
            date=planned.date,
            name=f"Semaine {planned.plan_week} · Séance {planned.session_number} · {planned.name}"[:100],
            description=f"Plan {GOAL_LABELS[profile.goal]}, semaine {planned.plan_week}. {planned.description}"[:2000],
            difficulty=max(1, min(4, planned.difficulty)),
        )
        db.add(session)
        db.flush()
        steps = _create_plan_steps(db, session.id, planned.steps)
        session.estimated_duration_min, session.estimated_distance_km = estimate_duration_and_distance(steps)
        db.add(session)
        generated_ids.append(str(session.id))

    state = _plan_state(db, user)
    if state is None:
        state = TrainingPlanState(user_id=user.id)
    state.plan_key = profile.goal
    state.race_date = profile.race_date
    state.generated_session_ids_json = json.dumps(generated_ids)
    db.add(state)
    db.commit()
    db.refresh(state)
    return state


def _profile_form_response(request: Request, profile_data: dict[str, Any], *, error: str | None = None, settings_mode: bool = False, saved: bool = False):
    auto_targets = calculate_pace_targets(profile_data["goal"], profile_data["ef_fast_sec_per_km"], profile_data["ef_slow_sec_per_km"]) if profile_data.get("ef_fast_sec_per_km") else {}
    targets = profile_data.get("pace_targets") or auto_targets
    return templates.TemplateResponse(
        request,
        "training_profile.html",
        {
            "user": request.state.user,
            "profile": profile_data,
            "goal_labels": GOAL_LABELS,
            "pace_rows": _pace_rows(targets, profile_data.get("manual_paces", [])),
            "error": error,
            "settings_mode": settings_mode,
            "saved": saved,
        },
    )


async def _form_or_error(request: Request) -> tuple[dict[str, Any] | None, Any, str | None]:
    form = await request.form()
    try:
        return _form_profile(form), form, None
    except ValueError as exc:
        return None, form, str(exc)


@router.get("/onboarding")
async def onboarding_page(request: Request, user: User = Depends(require_user), db: DBSession = Depends(get_session)):
    request.state.user = user
    profile = _profile(db, user)
    return _profile_form_response(request, _profile_context(profile))


@router.post("/onboarding/preview")
async def onboarding_preview(request: Request, user: User = Depends(require_user)):
    request.state.user = user
    data, form, error = await _form_or_error(request)
    context = _profile_context(None)
    if error:
        context.update({"goal": form.get("goal", context["goal"]), "race_date": form.get("race_date", ""), "sessions_per_week": form.get("sessions_per_week", 4), "ef_fast": form.get("ef_fast", ""), "ef_slow": form.get("ef_slow", "")})
        return _profile_form_response(request, context, error=error)
    try:
        plan, schedule = generate_schedule(data["goal"], data["race_date"], data["ef_fast_sec_per_km"], data["ef_slow_sec_per_km"], data["sessions_per_week"])
    except (ValueError, KeyError, ImportError) as exc:
        return _profile_form_response(request, context, error=f"Impossible de charger ce plan : {exc}")
    targets = calculate_pace_targets(data["goal"], data["ef_fast_sec_per_km"], data["ef_slow_sec_per_km"])
    profile_data = {
        **data,
        "race_date": data["race_date"].isoformat(),
        "ef_fast": seconds_to_pace(data["ef_fast_sec_per_km"]),
        "ef_slow": seconds_to_pace(data["ef_slow_sec_per_km"]),
        "pace_targets": targets,
        "manual_paces": [],
    }
    return templates.TemplateResponse(request, "training_profile_preview.html", {
        "user": user,
        "profile": profile_data,
        "goal_labels": GOAL_LABELS,
        "pace_rows": _pace_rows(targets, []),
        "plan": plan,
        "scheduled_count": len(schedule),
        "schedule_start": schedule[0].date if schedule else data["race_date"],
        "schedule_end": schedule[-1].date if schedule else data["race_date"],
    })


@router.post("/onboarding/save")
async def onboarding_save(request: Request, user: User = Depends(require_user), db: DBSession = Depends(get_session)):
    request.state.user = user
    form = await request.form()
    try:
        data = _form_profile(form)
        profile = _store_profile(db, user, data, form, _profile(db, user))
        user.pace_adjustment_pct = 0.0
        db.add(user)
        db.commit()
        _generate_plan(db, user, profile)
    except (ValueError, KeyError, ImportError) as exc:
        return _profile_form_response(request, _profile_context(_profile(db, user)), error=str(exc))
    return RedirectResponse(url="/", status_code=303)


@router.get("/settings/entrainement")
async def training_settings_page(request: Request, user: User = Depends(require_user), db: DBSession = Depends(get_session)):
    request.state.user = user
    profile = _profile(db, user)
    if profile is None:
        return RedirectResponse(url="/onboarding", status_code=303)
    return _profile_form_response(request, _profile_context(profile), settings_mode=True, saved=request.query_params.get("saved") == "1")


@router.post("/settings/entrainement")
async def save_training_settings(request: Request, user: User = Depends(require_user), db: DBSession = Depends(get_session)):
    request.state.user = user
    data, form, error = await _form_or_error(request)
    existing = _profile(db, user)
    if error:
        return _profile_form_response(request, _profile_context(existing), error=error, settings_mode=True)
    try:
        profile = _store_profile(db, user, data, form, existing)
        user.pace_adjustment_pct = 0.0
        db.add(user)
        db.commit()
        _generate_plan(db, user, profile)
    except (ValueError, KeyError, ImportError) as exc:
        return _profile_form_response(request, _profile_context(existing), error=str(exc), settings_mode=True)
    return RedirectResponse(url="/settings/entrainement?saved=1", status_code=303)

def _get_pace_data(profile: TrainingProfile) -> tuple[dict[str, int], tuple[int, int] | None]:
    targets = _json_dict(profile.pace_targets_json)
    ef_range = None
    if profile.ef_fast_sec_per_km and profile.ef_slow_sec_per_km:
        ef_range = (profile.ef_fast_sec_per_km, profile.ef_slow_sec_per_km)
    return targets, ef_range


def _existing_sessions_by_import_week(
    db: DBSession,
    user: User,
    start_date: date,
    week_numbers: set[int],
) -> dict[int, list[Session]]:
    """Retourne les séances déjà présentes dans les semaines importées."""
    existing: dict[int, list[Session]] = {}
    for week_number in sorted(week_numbers):
        week_start = start_date + timedelta(weeks=week_number - 1)
        week_end = week_start + timedelta(days=6)
        sessions = db.exec(
            select(Session)
            .where(Session.athlete_id == user.id)
            .where(Session.date >= week_start)
            .where(Session.date <= week_end)
            .order_by(Session.date)
        ).all()
        if sessions:
            existing[week_number] = sessions
    return existing


def _text_preview_context(
    request: Request,
    user: User,
    profile: TrainingProfile,
    text: str,
    start_date: date,
    *,
    error: str | None = None,
    db: DBSession | None = None,
):
    targets, ef_range = _get_pace_data(profile)
    try:
        result = parse_workout_text(
            text,
            start_date=start_date,
            pace_targets=targets,
            ef_range=ef_range,
            sessions_per_week=profile.sessions_per_week,
        )
    except WorkoutTextError as exc:
        return templates.TemplateResponse(
            request,
            "workout_text.html",
            {
                "user": user,
                "profile": _profile_context(profile),
                "text": text,
                "start_date": start_date.isoformat(),
                "error": error or str(exc),
            },
            status_code=400,
        )

    warnings = []
    dates = [workout.date for workout in result.workouts]
    if len(set(dates)) != len(dates):
        error = error or "Plusieurs séances sont prévues le même jour. Modifie le texte avant de confirmer."
    existing_weeks = (
        _existing_sessions_by_import_week(
            db,
            user,
            start_date,
            {workout.week_number for workout in result.workouts},
        )
        if db is not None and result.workouts
        else {}
    )
    if existing_weeks:
        week_labels = ", ".join(str(week) for week in sorted(existing_weeks))
        existing_count = sum(len(sessions) for sessions in existing_weeks.values())
        warnings.append(
            f"Des séances existent déjà dans la ou les semaines {week_labels} "
            f"({existing_count} séance{'s' if existing_count != 1 else ''}). "
            "Si tu confirmes l'écrasement, toutes les séances de ces semaines seront remplacées."
        )
    if len(set(dates)) != len(dates):
        warnings.append("Plusieurs séances sont prévues le même jour.")

    rows = []
    for workout in result.workouts:
        steps = []
        for step in workout.steps:
            if step.kind == "repeat":
                steps.append(f"Bloc répété {step.repeat_count} fois")
                continue
            duration = (
                f"{step.duration_value / 60:g} min" if step.duration_type == "time"
                else f"{step.duration_value / 1000:g} km" if step.duration_value >= 1000
                else f"{step.duration_value:g} m"
            )
            pace = ""
            if step.target_type == "pace" and step.pace_fast_seconds_per_km is not None:
                fast = seconds_to_pace(step.pace_fast_seconds_per_km)
                slow = seconds_to_pace(step.pace_slow_seconds_per_km)
                pace = f" · {fast}/km" if fast == slow else f" · {fast}–{slow}/km"
            steps.append(f"{duration} {step.label}{pace}")
        rows.append({
            "date": workout.date,
            "name": workout.name,
            "steps": steps,
            "week_number": workout.week_number,
            "session_number": workout.session_number,
        })
    omitted_rows = [
        {"week_number": workout.week_number, "name": workout.name, "session_number": workout.session_number}
        for workout in result.omitted_workouts
    ]
    return templates.TemplateResponse(
        request,
        "workout_text_preview.html",
        {
            "user": user,
            "text": text,
            "start_date": start_date.isoformat(),
            "rows": rows,
            "omitted_rows": omitted_rows,
            "warnings": list(dict.fromkeys(warnings)),
            "error": error,
            "can_confirm": error is None,
            "existing_weeks": sorted(existing_weeks),
            "profile": _profile_context(profile),
            "race_date": profile.race_date,
        },
    )


@router.get("/programme/texte")
async def workout_text_page(
    request: Request,
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
):
    profile = _profile(db, user)
    if profile is None:
        return RedirectResponse(url="/onboarding", status_code=303)
    today = date.today()
    start_date = today + timedelta(days=7 - today.weekday())
    return templates.TemplateResponse(
        request,
        "workout_text.html",
        {
            "user": user,
            "profile": _profile_context(profile),
            "text": "",
            "start_date": start_date.isoformat(),
            "error": None,
        },
    )


@router.post("/programme/texte/apercu")
async def workout_text_preview(
    request: Request,
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
):
    profile = _profile(db, user)
    if profile is None:
        return RedirectResponse(url="/onboarding", status_code=303)
    form = await request.form()
    text = str(form.get("program_text") or "")
    if len(text) > 20000:
        return templates.TemplateResponse(
            request,
            "workout_text.html",
            {"user": user, "profile": _profile_context(profile), "text": text[:20000], "start_date": str(form.get("start_date") or ""), "error": "Le texte dépasse la limite de 20 000 caractères."},
            status_code=400,
        )
    try:
        start_date = date.fromisoformat(str(form.get("start_date") or ""))
    except ValueError:
        return templates.TemplateResponse(
            request,
            "workout_text.html",
            {"user": user, "profile": _profile_context(profile), "text": text, "start_date": "", "error": "Renseigne une date de début valide."},
            status_code=400,
        )
    if start_date.weekday() != 0:
        return templates.TemplateResponse(
            request,
            "workout_text.html",
            {
                "user": user,
                "profile": _profile_context(profile),
                "text": text,
                "start_date": start_date.isoformat(),
                "error": "La date de début doit être un lundi.",
            },
            status_code=400,
        )
    return _text_preview_context(request, user, profile, text, start_date, db=db)


@router.post("/programme/texte/confirmer")
async def confirm_workout_text(
    request: Request,
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
):
    profile = _profile(db, user)
    if profile is None:
        return RedirectResponse(url="/onboarding", status_code=303)
    form = await request.form()
    text = str(form.get("program_text") or "")
    if len(text) > 20000:
        raise HTTPException(400, "Le texte dépasse la limite de 20 000 caractères.")
    try:
        start_date = date.fromisoformat(str(form.get("start_date") or ""))
    except ValueError:
        raise HTTPException(400, "Date de début invalide.")
    if start_date.weekday() != 0:
        return templates.TemplateResponse(
            request,
            "workout_text.html",
            {
                "user": user,
                "profile": _profile_context(profile),
                "text": text,
                "start_date": start_date.isoformat(),
                "error": "La date de début doit être un lundi.",
            },
            status_code=400,
        )

    targets, ef_range = _get_pace_data(profile)
    try:
        parsed = parse_workout_text(
            text,
            start_date=start_date,
            pace_targets=targets,
            ef_range=ef_range,
            sessions_per_week=profile.sessions_per_week,
        )
    except WorkoutTextError as exc:
        return _text_preview_context(request, user, profile, text, start_date, error=str(exc))

    dates = [workout.date for workout in parsed.workouts]
    if len(set(dates)) != len(dates):
        return _text_preview_context(
            request, user, profile, text, start_date,
            error="Plusieurs séances sont prévues le même jour. Modifie le texte avant de confirmer.",
            db=db,
        )
    existing_weeks = _existing_sessions_by_import_week(
        db,
        user,
        start_date,
        {workout.week_number for workout in parsed.workouts},
    ) if dates else {}
    overwrite_existing = str(form.get("overwrite_existing") or "").lower() in ("1", "true", "on", "yes")
    if existing_weeks and not overwrite_existing:
        week_labels = ", ".join(str(week) for week in sorted(existing_weeks))
        return _text_preview_context(
            request, user, profile, text, start_date,
            error=(
                f"Des séances existent déjà dans la ou les semaines {week_labels}. "
                "Coche l'écrasement si tu veux remplacer ces semaines, ou modifie la date de départ."
            ),
            db=db,
        )

    if existing_weeks:
        delete_sessions_safely(db, [session for sessions in existing_weeks.values() for session in sessions])

    for workout in parsed.workouts:
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
    db.commit()
    return RedirectResponse(url="/", status_code=303)
