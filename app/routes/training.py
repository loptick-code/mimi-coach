"""Onboarding, profil d'allures et import texte déterministe des séances."""
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
from app.services.workout_builder import (
    estimate_duration_and_distance,
    pace_min_per_km_to_m_per_s,
)
from app.services.workout_text_parser import (
    ParsedWorkout,
    WorkoutTextError,
    parse_workout_text,
)
from app.templates import templates

router = APIRouter()

EXPERIENCE_CHOICES = {"beginner", "returning", "regular"}
GOAL_LABELS = {"10k": "10 km", "half": "Semi-marathon", "marathon": "Marathon"}
ALL_DAYS = [
    ("lundi", "Lundi"), ("mardi", "Mardi"), ("mercredi", "Mercredi"),
    ("jeudi", "Jeudi"), ("vendredi", "Vendredi"), ("samedi", "Samedi"),
    ("dimanche", "Dimanche"),
]
PACE_KEYS = list(PACE_LABELS)


def _profile(db: DBSession, user: User) -> TrainingProfile | None:
    return db.exec(
        select(TrainingProfile).where(TrainingProfile.user_id == user.id)
    ).first()


def _json_dict(raw: str | None) -> dict[str, int]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(key): int(number) for key, number in value.items() if isinstance(number, (int, float))}


def _json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _form_profile(form: Any) -> dict[str, Any]:
    goal = str(form.get("goal") or "").strip()
    if not supported_goal(goal):
        raise ValueError("Choisis un objectif parmi 10 km, semi-marathon ou marathon.")

    experience = str(form.get("experience") or "regular").strip()
    if experience not in EXPERIENCE_CHOICES:
        raise ValueError("Choisis un niveau d'expérience valide.")

    try:
        runs_per_week = int(form.get("current_runs_per_week") or 0)
    except (TypeError, ValueError):
        raise ValueError("Le nombre de sorties actuelles doit être un nombre entier.")
    if not 0 <= runs_per_week <= 7:
        raise ValueError("Le nombre de sorties actuelles doit être entre 0 et 7.")

    longest_raw = str(form.get("longest_run_km") or "").strip().replace(",", ".")
    try:
        longest_run = float(longest_raw) if longest_raw else None
    except ValueError:
        raise ValueError("La sortie la plus longue doit être une distance en kilomètres.")
    if longest_run is not None and not 0 <= longest_run <= 100:
        raise ValueError("La sortie la plus longue doit être entre 0 et 100 km.")

    try:
        sessions_per_week = int(form.get("sessions_per_week") or 4)
    except (TypeError, ValueError):
        raise ValueError("Choisis 3 ou 4 séances par semaine.")
    if sessions_per_week not in (3, 4):
        raise ValueError("Choisis 3 ou 4 séances par semaine.")

    available_days = [str(day).strip().lower() for day in form.getlist("available_days")]
    valid_days = {day for day, _ in ALL_DAYS}
    if any(day not in valid_days for day in available_days):
        raise ValueError("Un des jours sélectionnés n'est pas reconnu.")
    available_days = list(dict.fromkeys(available_days))
    if available_days and len(available_days) < sessions_per_week:
        raise ValueError(f"Sélectionne au moins {sessions_per_week} jours disponibles, ou ne précise aucun jour.")

    race_date_raw = str(form.get("race_date") or "").strip()
    try:
        race_date = date.fromisoformat(race_date_raw) if race_date_raw else None
    except ValueError:
        raise ValueError("La date de course est invalide.")

    ef_fast_raw = str(form.get("ef_fast") or "").strip()
    ef_slow_raw = str(form.get("ef_slow") or "").strip()
    if bool(ef_fast_raw) != bool(ef_slow_raw):
        raise ValueError("Renseigne les deux bornes de l'EF, ou laisse les deux vides.")
    if ef_fast_raw:
        ef_fast = pace_to_seconds(ef_fast_raw)
        ef_slow = pace_to_seconds(ef_slow_raw)
        validate_ef_range(ef_fast, ef_slow)
    else:
        ef_fast = ef_slow = None

    return {
        "goal": goal,
        "race_date": race_date,
        "experience": experience,
        "current_runs_per_week": runs_per_week,
        "longest_run_km": longest_run,
        "sessions_per_week": sessions_per_week,
        "available_days": available_days,
        "ef_fast_sec_per_km": ef_fast,
        "ef_slow_sec_per_km": ef_slow,
    }


def _profile_context(profile: TrainingProfile | None) -> dict[str, Any]:
    if profile is None:
        return {
            "goal": "10k", "race_date": "", "experience": "regular",
            "current_runs_per_week": 3, "longest_run_km": "",
            "sessions_per_week": 4, "available_days": [],
            "ef_fast": "", "ef_slow": "", "pace_targets": {}, "manual_paces": [],
        }
    return {
        "goal": profile.goal,
        "race_date": profile.race_date.isoformat() if profile.race_date else "",
        "experience": profile.experience,
        "current_runs_per_week": profile.current_runs_per_week,
        "longest_run_km": profile.longest_run_km if profile.longest_run_km is not None else "",
        "sessions_per_week": profile.sessions_per_week,
        "available_days": [day for day in profile.available_days.split(",") if day],
        "ef_fast": seconds_to_pace(profile.ef_fast_sec_per_km),
        "ef_slow": seconds_to_pace(profile.ef_slow_sec_per_km),
        "pace_targets": _json_dict(profile.pace_targets_json),
        "manual_paces": _json_list(profile.manual_paces_json),
    }


def _pace_rows(targets: dict[str, int], manual: list[str] | set[str]) -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "label": PACE_LABELS[key],
            "value": seconds_to_pace(targets.get(key)),
            "manual": key in manual,
        }
        for key in PACE_KEYS
    ]


def _store_profile(
    db: DBSession,
    user: User,
    data: dict[str, Any],
    form: Any,
    existing: TrainingProfile | None,
) -> TrainingProfile:
    auto_targets = {}
    if data["ef_fast_sec_per_km"] is not None:
        auto_targets = calculate_pace_targets(
            data["goal"], data["ef_fast_sec_per_km"], data["ef_slow_sec_per_km"]
        )

    reset_all = str(form.get("reset_auto_paces") or "").lower() in ("1", "true", "on", "yes")
    new_targets: dict[str, int] = {}
    manual_paces: set[str] = set()

    for key in PACE_KEYS:
        raw_value = str(form.get(f"pace_{key}") or "").strip()
        manual_flag = str(form.get(f"manual_{key}") or "").lower() in ("1", "true", "on", "yes")
        if raw_value:
            try:
                submitted_seconds = pace_to_seconds(raw_value)
            except ValueError:
                raise ValueError(f"Allure invalide pour {PACE_LABELS[key]}.")
        else:
            submitted_seconds = None

        if not reset_all and manual_flag and submitted_seconds is not None:
            new_targets[key] = submitted_seconds
            manual_paces.add(key)
        elif key in auto_targets:
            new_targets[key] = auto_targets[key]

    if existing is None:
        existing = TrainingProfile(user_id=user.id)
    existing.goal = data["goal"]
    existing.race_date = data["race_date"]
    existing.experience = data["experience"]
    existing.current_runs_per_week = data["current_runs_per_week"]
    existing.longest_run_km = data["longest_run_km"]
    existing.sessions_per_week = data["sessions_per_week"]
    existing.available_days = ",".join(data["available_days"])
    existing.ef_fast_sec_per_km = data["ef_fast_sec_per_km"]
    existing.ef_slow_sec_per_km = data["ef_slow_sec_per_km"]
    existing.pace_targets_json = json.dumps(new_targets, ensure_ascii=False)
    existing.manual_paces_json = json.dumps(sorted(manual_paces), ensure_ascii=False)
    db.add(existing)
    db.commit()
    db.refresh(existing)
    return existing


def _profile_form_response(
    request: Request,
    profile_data: dict[str, Any],
    *,
    error: str | None = None,
    settings_mode: bool = False,
    saved: bool = False,
):
    auto_targets = {}
    if profile_data.get("ef_fast_sec_per_km") is not None:
        auto_targets = calculate_pace_targets(
            profile_data["goal"], profile_data["ef_fast_sec_per_km"], profile_data["ef_slow_sec_per_km"]
        )
    targets = profile_data.get("pace_targets") or auto_targets
    return templates.TemplateResponse(
        request,
        "training_profile.html",
        {
            "user": request.state.user,
            "profile": profile_data,
            "days": ALL_DAYS,
            "goal_labels": GOAL_LABELS,
            "pace_rows": _pace_rows(targets, profile_data.get("manual_paces", [])),
            "auto_targets": auto_targets,
            "error": error,
            "settings_mode": settings_mode,
            "saved": saved,
        },
    )


def _get_user(request: Request) -> User:
    return request.state.user


async def _form_or_error(request: Request) -> tuple[dict[str, Any] | None, Any, str | None]:
    form = await request.form()
    try:
        return _form_profile(form), form, None
    except ValueError as exc:
        return None, form, str(exc)


@router.get("/onboarding")
async def onboarding_page(
    request: Request,
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
):
    request.state.user = user
    return _profile_form_response(request, _profile_context(_profile(db, user)))


@router.post("/onboarding/preview")
async def onboarding_preview(
    request: Request,
    user: User = Depends(require_user),
):
    request.state.user = user
    data, form, error = await _form_or_error(request)
    if error:
        context = _profile_context(None)
        context.update({key: form.get(key, context.get(key, "")) for key in (
            "goal", "race_date", "experience", "current_runs_per_week", "longest_run_km", "sessions_per_week", "ef_fast", "ef_slow"
        )})
        context["available_days"] = [str(day) for day in form.getlist("available_days")]
        return _profile_form_response(request, context, error=error)

    targets = calculate_pace_targets(data["goal"], data["ef_fast_sec_per_km"], data["ef_slow_sec_per_km"]) if data["ef_fast_sec_per_km"] else {}
    profile_data = {
        **data,
        "race_date": data["race_date"].isoformat() if data["race_date"] else "",
        "available_days": data["available_days"],
        "ef_fast": seconds_to_pace(data["ef_fast_sec_per_km"]),
        "ef_slow": seconds_to_pace(data["ef_slow_sec_per_km"]),
        "pace_targets": targets,
        "manual_paces": [],
    }
    return templates.TemplateResponse(
        request,
        "training_profile_preview.html",
        {
            "user": user,
            "profile": profile_data,
            "goal_labels": GOAL_LABELS,
            "pace_rows": _pace_rows(targets, []),
            "has_ef": bool(data["ef_fast_sec_per_km"]),
            "has_auto_reference": data["goal"] in ("10k", "half"),
            "base_warning": (
                data["experience"] in ("beginner", "returning")
                or data["current_runs_per_week"] < data["sessions_per_week"]
            ),
        },
    )


@router.post("/onboarding/save")
async def onboarding_save(
    request: Request,
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
):
    form = await request.form()
    try:
        data = _form_profile(form)
        existing = _profile(db, user)
        _store_profile(db, user, data, form, existing)
    except ValueError as exc:
        request.state.user = user
        context = _profile_context(None)
        context.update({key: form.get(key, context.get(key, "")) for key in (
            "goal", "race_date", "experience", "current_runs_per_week", "longest_run_km", "sessions_per_week", "ef_fast", "ef_slow"
        )})
        context["available_days"] = [str(day) for day in form.getlist("available_days")]
        return _profile_form_response(request, context, error=str(exc))
    return RedirectResponse(url="/programme/texte", status_code=303)


@router.get("/settings/entrainement")
async def training_settings_page(
    request: Request,
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
):
    request.state.user = user
    profile = _profile(db, user)
    if profile is None:
        return RedirectResponse(url="/onboarding", status_code=303)
    context = _profile_context(profile)
    return _profile_form_response(
        request, context, settings_mode=True, saved=request.query_params.get("saved") == "1"
    )


@router.post("/settings/entrainement")
async def save_training_settings(
    request: Request,
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
):
    request.state.user = user
    data, form, error = await _form_or_error(request)
    existing = _profile(db, user)
    if error:
        context = _profile_context(existing)
        context.update({key: form.get(key, context.get(key, "")) for key in (
            "goal", "race_date", "experience", "current_runs_per_week", "longest_run_km", "sessions_per_week", "ef_fast", "ef_slow"
        )})
        context["available_days"] = [str(day) for day in form.getlist("available_days")]
        return _profile_form_response(request, context, error=error, settings_mode=True)
    try:
        _store_profile(db, user, data, form, existing)
    except ValueError as exc:
        context = _profile_context(existing)
        return _profile_form_response(request, context, error=str(exc), settings_mode=True)
    return RedirectResponse(url="/settings/entrainement?saved=1", status_code=303)


def _get_pace_data(profile: TrainingProfile) -> tuple[dict[str, int], tuple[int, int] | None]:
    targets = _json_dict(profile.pace_targets_json)
    ef_range = None
    if profile.ef_fast_sec_per_km and profile.ef_slow_sec_per_km:
        ef_range = (profile.ef_fast_sec_per_km, profile.ef_slow_sec_per_km)
    return targets, ef_range


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

    available_days = {day for day in profile.available_days.split(",") if day}
    warnings = []
    for workout in result.workouts:
        if available_days and workout.weekday_label not in available_days:
            warnings.append(f"{workout.weekday_label.capitalize()} n'est pas dans tes jours disponibles.")
    dates = [workout.date for workout in result.workouts]
    if len(set(dates)) != len(dates):
        error = error or "Plusieurs séances sont prévues le même jour. Modifie le texte avant de confirmer."
    if db is not None and dates:
        collisions = db.exec(
            select(Session).where(Session.athlete_id == user.id).where(Session.date.in_(dates))
        ).all()
        if collisions:
            error = error or "Une ou plusieurs dates ont déjà une séance. Modifie la date de début ou supprime les doublons avant de confirmer."
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
            "weekday_label": workout.weekday_label,
        })
    omitted_rows = [
        {"week_number": workout.week_number, "weekday_label": workout.weekday_label, "name": workout.name, "session_number": workout.session_number}
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
    collisions = db.exec(
        select(Session).where(Session.athlete_id == user.id).where(Session.date.in_(dates))
    ).all() if dates else []
    if collisions:
        return _text_preview_context(
            request, user, profile, text, start_date,
            error="Une ou plusieurs dates ont déjà une séance. Modifie la date de début ou supprime les doublons avant de confirmer.",
            db=db,
        )

    for workout in parsed.workouts:
        session = Session(
            athlete_id=user.id,
            date=workout.date,
            name=workout.name[:100],
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
