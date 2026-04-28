"""Routes d'affichage et de CRUD des seances."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session as DBSession
from sqlmodel import select

from app.auth import current_user, require_coach, require_user
from app.db import get_session
from app.models import (
    FEEDBACK_DELTA,
    PACE_ADJUSTMENT_BOUND,
    DurationType,
    Session,
    SessionFeedback,
    Step,
    StepType,
    TargetType,
    User,
    UserRole,
)
from app.services import garmin_client
from app.services.workout_builder import (
    estimate_duration_and_distance,
    pace_str_to_min_per_km,
    pace_min_per_km_to_m_per_s,
)
from app.templates import templates

router = APIRouter()


def _week_bounds(reference: date) -> tuple[date, date]:
    """Retourne (lundi, dimanche) de la semaine contenant `reference`."""
    monday = reference - timedelta(days=reference.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _resolve_target_athlete(db: DBSession, viewer: User) -> User:
    """Si le viewer est coach, on cible l'unique athlete. Sinon viewer."""
    if viewer.role == UserRole.athlete:
        return viewer
    athlete = db.exec(
        select(User).where(User.role == UserRole.athlete)
    ).first()
    if athlete is None:
        raise HTTPException(404, "Aucun athlete configure")
    return athlete


@router.get("/")
async def home(
    request: Request,
    week: Optional[str] = None,
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
):
    """Vue 'Cette semaine' (par defaut) - liste des seances."""
    today = date.today()
    if week:
        try:
            today = date.fromisoformat(week)
        except ValueError:
            pass
    monday, sunday = _week_bounds(today)

    athlete = _resolve_target_athlete(db, user)

    sessions = db.exec(
        select(Session)
        .where(Session.athlete_id == athlete.id)
        .where(Session.date >= monday)
        .where(Session.date <= sunday)
        .order_by(Session.date)
    ).all()

    total_duration = sum((s.estimated_duration_min or 0) for s in sessions)
    total_distance = sum((s.estimated_distance_km or 0) for s in sessions)

    # Progression : ce qui est fait (séance validée par l'athlète)
    done_sessions = [s for s in sessions if s.feedback is not None]
    done_count = len(done_sessions)
    done_duration = sum((s.estimated_duration_min or 0) for s in done_sessions)
    done_distance = sum((s.estimated_distance_km or 0) for s in done_sessions)

    prev_week = (monday - timedelta(days=7)).isoformat()
    next_week = (monday + timedelta(days=7)).isoformat()

    return templates.TemplateResponse(
        request,
        "week.html",
        {
            "user": user,
            "athlete": athlete,
            "monday": monday,
            "sunday": sunday,
            "sessions": sessions,
            "total_count": len(sessions),
            "total_duration": total_duration,
            "total_distance": total_distance,
            "done_count": done_count,
            "done_duration": done_duration,
            "done_distance": done_distance,
            "prev_week": prev_week,
            "next_week": next_week,
            "is_current_week": monday <= date.today() <= sunday,
        },
    )


@router.get("/stats")
async def stats(
    request: Request,
    period: str = "month",
    year: Optional[int] = None,
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
):
    """Recap des seances : ce mois / cette annee / tout, avec realise vs prevu."""
    today = date.today()
    athlete = _resolve_target_athlete(db, user)

    if period not in ("month", "year", "all"):
        period = "month"
    target_year = year or today.year

    # Bornes du periode + label affichable
    if period == "month":
        start = today.replace(day=1)
        if start.month == 12:
            end = date(start.year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(start.year, start.month + 1, 1) - timedelta(days=1)
        period_label = today.strftime("%B %Y")
    elif period == "year":
        start = date(target_year, 1, 1)
        end = date(target_year, 12, 31)
        period_label = str(target_year)
    else:  # all
        start = date(2000, 1, 1)
        end = date(2100, 1, 1)
        period_label = "Tout"

    sessions = db.exec(
        select(Session)
        .where(Session.athlete_id == athlete.id)
        .where(Session.date >= start)
        .where(Session.date <= end)
        .order_by(Session.date)
    ).all()

    # Totaux planifies (toutes les seances du periode)
    planned_count = len(sessions)
    planned_duration_min = sum((s.estimated_duration_min or 0) for s in sessions)
    planned_distance_km = sum((s.estimated_distance_km or 0) for s in sessions)

    # Totaux realises (seules les seances avec actuals Garmin)
    done = [s for s in sessions if s.actual_duration_sec and s.actual_distance_m]
    done_count = len(done)
    done_duration_sec = sum(s.actual_duration_sec or 0 for s in done)
    done_distance_m = sum(s.actual_distance_m or 0 for s in done)
    done_avg_pace_sec_per_km = (
        int(done_duration_sec / (done_distance_m / 1000))
        if done_distance_m > 0
        else 0
    )

    # Validees (feedback non null) - peut-etre sans actuals
    validated_count = sum(1 for s in sessions if s.feedback is not None)

    # Breakdown mensuel (uniquement pour year/all)
    monthly_breakdown: list[dict] = []
    if period in ("year", "all") and sessions:
        from collections import defaultdict
        buckets: dict[tuple[int, int], dict] = defaultdict(
            lambda: {"count": 0, "duration_sec": 0, "distance_m": 0, "planned_km": 0.0}
        )
        for s in sessions:
            key = (s.date.year, s.date.month)
            buckets[key]["planned_km"] += s.estimated_distance_km or 0
            if s.actual_duration_sec and s.actual_distance_m:
                buckets[key]["count"] += 1
                buckets[key]["duration_sec"] += s.actual_duration_sec
                buckets[key]["distance_m"] += s.actual_distance_m
        for (yr, mo), v in sorted(buckets.items()):
            monthly_breakdown.append({
                "year": yr,
                "month": mo,
                "label": date(yr, mo, 1).strftime("%B %Y"),
                "count": v["count"],
                "duration_min": int(v["duration_sec"] / 60),
                "distance_km": v["distance_m"] / 1000,
                "planned_km": v["planned_km"],
            })

    # Evolution allure EF : un point par seance EF realisee
    # Filtre sur les seances dont le nom contient "Endurance" (couvre EF + EF active).
    ef_points: list[dict] = []
    for s in sessions:
        if not s.actual_duration_sec or not s.actual_distance_m:
            continue
        name_lower = (s.name or "").lower()
        if "endurance" not in name_lower:
            continue
        km = s.actual_distance_m / 1000
        if km <= 0:
            continue
        pace_sec = int(s.actual_duration_sec / km)
        ef_points.append({
            "date": s.date,
            "date_iso": s.date.isoformat(),
            "date_label": s.date.strftime("%d %b"),
            "pace_sec": pace_sec,
            "pace_label": f"{pace_sec // 60}:{pace_sec % 60:02d}",
            "name": s.name,
        })

    return templates.TemplateResponse(
        request,
        "stats.html",
        {
            "user": user,
            "athlete": athlete,
            "period": period,
            "period_label": period_label,
            "year": target_year,
            "today_year": today.year,
            "planned_count": planned_count,
            "planned_duration_min": planned_duration_min,
            "planned_distance_km": planned_distance_km,
            "done_count": done_count,
            "done_duration_sec": done_duration_sec,
            "done_distance_km": done_distance_m / 1000,
            "done_avg_pace_sec_per_km": done_avg_pace_sec_per_km,
            "validated_count": validated_count,
            "monthly_breakdown": monthly_breakdown,
            "ef_points": ef_points,
        },
    )


@router.get("/sessions/{session_id}")
async def session_detail(
    request: Request,
    session_id: int,
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
):
    s = db.get(Session, session_id)
    if s is None:
        raise HTTPException(404)
    if user.role == UserRole.athlete and s.athlete_id != user.id:
        raise HTTPException(403)

    athlete = db.get(User, s.athlete_id)

    # Ajustement effectif sur cette seance :
    # - Si validee : on fige l'ajustement applique au moment de la validation
    # - Sinon : ajustement courant de l'athlete (s'applique aux futures seances)
    if s.feedback is not None:
        applied_adj = s.pace_adjustment_at_validation or 0.0
    else:
        applied_adj = (athlete.pace_adjustment_pct or 0.0) if athlete else 0.0

    return templates.TemplateResponse(
        request,
        "session_detail.html",
        {
            "user": user,
            "session": s,
            "athlete": athlete,
            "applied_adjustment_pct": applied_adj,
            "can_edit": True,
        },
    )


@router.post("/sessions/{session_id}/feedback")
async def submit_feedback(
    session_id: int,
    feedback: str = Form(...),
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
):
    """Valide une seance + enregistre le ressenti.

    Le ressenti met a jour le pace_adjustment_pct global de l'athlete
    (sauf si la seance etait deja validee, auquel cas on revient sur l'ancien
    delta avant d'appliquer le nouveau).
    """
    s = db.get(Session, session_id)
    if s is None:
        raise HTTPException(404)
    if user.role == UserRole.athlete and s.athlete_id != user.id:
        raise HTTPException(403)

    try:
        new_fb = SessionFeedback(feedback)
    except ValueError:
        raise HTTPException(400, "Feedback invalide")

    athlete = db.get(User, s.athlete_id)
    if athlete is None:
        raise HTTPException(404)

    # Annule l'effet du feedback precedent s'il existe (revient a l'etat pre-validation)
    if s.feedback is not None:
        athlete.pace_adjustment_pct = (athlete.pace_adjustment_pct or 0.0) - FEEDBACK_DELTA[s.feedback]

    # Snapshot AVANT le nouveau delta : ce sont les allures avec lesquelles
    # cette seance a ete prescrite/courue.
    snapshot = athlete.pace_adjustment_pct or 0.0

    # Applique le nouveau feedback (impacte les futures seances uniquement)
    athlete.pace_adjustment_pct = (athlete.pace_adjustment_pct or 0.0) + FEEDBACK_DELTA[new_fb]

    s.feedback = new_fb
    s.validated_at = datetime.utcnow()
    s.pace_adjustment_at_validation = snapshot

    # Tente de recuperer l'activite Garmin du jour pour stocker le realise.
    # Echec silencieux : la validation reste valide meme si Garmin ne repond pas
    # ou si aucune activite n'est trouvee (montre pas synchro, run sans GPS, etc.)
    garmin_status = "ok"
    if athlete.garmin_token_encrypted:
        def _persist_token(new_token: bytes) -> None:
            athlete.garmin_token_encrypted = new_token
            db.add(athlete)

        try:
            activity = garmin_client.fetch_running_activity_for_date(
                athlete.garmin_token_encrypted,
                s.date,
                on_token_refreshed=_persist_token,
            )
            if activity is not None:
                s.garmin_activity_id = activity.activity_id
                s.actual_duration_sec = activity.duration_sec
                s.actual_distance_m = activity.distance_m
            else:
                garmin_status = "no_activity"
        except garmin_client.GarminError:
            garmin_status = "garmin_error"
    else:
        garmin_status = "garmin_not_connected"

    db.add(athlete)
    db.add(s)
    db.commit()
    return RedirectResponse(
        url=f"/sessions/{session_id}?success=feedback&garmin={garmin_status}",
        status_code=303,
    )


@router.post("/sessions/{session_id}/resync-garmin")
async def resync_garmin(
    session_id: int,
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
):
    """Re-importe le realise depuis Garmin pour une seance deja validee.

    Utile quand la montre n'avait pas synchro au moment de la validation.
    """
    s = db.get(Session, session_id)
    if s is None:
        raise HTTPException(404)
    if user.role == UserRole.athlete and s.athlete_id != user.id:
        raise HTTPException(403)

    athlete = db.get(User, s.athlete_id)
    if athlete is None or not athlete.garmin_token_encrypted:
        return RedirectResponse(
            url=f"/sessions/{session_id}?error=garmin_not_connected",
            status_code=303,
        )

    def _persist_token(new_token: bytes) -> None:
        athlete.garmin_token_encrypted = new_token
        db.add(athlete)

    try:
        activity = garmin_client.fetch_running_activity_for_date(
            athlete.garmin_token_encrypted,
            s.date,
            on_token_refreshed=_persist_token,
        )
    except garmin_client.GarminError:
        db.commit()
        return RedirectResponse(
            url=f"/sessions/{session_id}?error=garmin_error",
            status_code=303,
        )

    if activity is None:
        db.commit()
        return RedirectResponse(
            url=f"/sessions/{session_id}?error=no_activity",
            status_code=303,
        )

    s.garmin_activity_id = activity.activity_id
    s.actual_duration_sec = activity.duration_sec
    s.actual_distance_m = activity.distance_m
    db.add(s)
    db.commit()
    return RedirectResponse(
        url=f"/sessions/{session_id}?success=resynced",
        status_code=303,
    )


@router.post("/sessions/{session_id}/unvalidate")
async def unvalidate(
    session_id: int,
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
):
    """Annule la validation - revient en arriere sur l'ajustement."""
    s = db.get(Session, session_id)
    if s is None:
        raise HTTPException(404)
    if user.role == UserRole.athlete and s.athlete_id != user.id:
        raise HTTPException(403)

    athlete = db.get(User, s.athlete_id)
    if s.feedback is not None and athlete is not None:
        athlete.pace_adjustment_pct = (athlete.pace_adjustment_pct or 0.0) - FEEDBACK_DELTA[s.feedback]
        db.add(athlete)
    s.feedback = None
    s.validated_at = None
    s.pace_adjustment_at_validation = None
    db.add(s)
    db.commit()
    return RedirectResponse(url=f"/sessions/{session_id}", status_code=303)


# --- Admin (coach uniquement) ---


@router.get("/admin")
async def admin_home(
    request: Request,
    user: User = Depends(require_coach),
    db: DBSession = Depends(get_session),
):
    athlete = _resolve_target_athlete(db, user)
    today = date.today()
    sessions_list = db.exec(
        select(Session)
        .where(Session.athlete_id == athlete.id)
        .where(Session.date >= today - timedelta(days=14))
        .order_by(Session.date)
    ).all()

    # Regroupe par semaine ISO (Lun -> Dim)
    weeks: list[dict] = []
    seen_weeks: dict[date, dict] = {}
    for s in sessions_list:
        monday, sunday = _week_bounds(s.date)
        if monday not in seen_weeks:
            group = {
                "monday": monday,
                "sunday": sunday,
                "sessions": [],
                "is_current": monday <= today <= sunday,
                "is_future": monday > today,
            }
            seen_weeks[monday] = group
            weeks.append(group)
        seen_weeks[monday]["sessions"].append(s)

    # Calcule totaux par semaine
    for w in weeks:
        w["total_duration"] = sum((s.estimated_duration_min or 0) for s in w["sessions"])
        w["total_distance"] = sum((s.estimated_distance_km or 0) for s in w["sessions"])
        w["done_count"] = sum(1 for s in w["sessions"] if s.feedback is not None)

    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "user": user,
            "athlete": athlete,
            "weeks": weeks,
            "total_sessions": len(sessions_list),
        },
    )


@router.get("/admin/sessions/new")
async def admin_new_session(
    request: Request,
    user: User = Depends(require_coach),
    db: DBSession = Depends(get_session),
):
    athlete = _resolve_target_athlete(db, user)
    return templates.TemplateResponse(
        request,
        "session_form.html",
        {
            "user": user,
            "athlete": athlete,
            "session": None,
            "today": date.today().isoformat(),
        },
    )


@router.get("/admin/sessions/{session_id}/edit")
async def admin_edit_session(
    request: Request,
    session_id: int,
    user: User = Depends(require_coach),
    db: DBSession = Depends(get_session),
):
    s = db.get(Session, session_id)
    if s is None:
        raise HTTPException(404)
    athlete = db.get(User, s.athlete_id)
    return templates.TemplateResponse(
        request,
        "session_form.html",
        {"user": user, "athlete": athlete, "session": s},
    )


@router.post("/admin/sessions")
async def admin_create_session(
    request: Request,
    user: User = Depends(require_coach),
    db: DBSession = Depends(get_session),
):
    """Cree (ou met a jour si session_id present) une seance avec ses steps.

    Le formulaire envoie un payload structure (encoded en form fields :
        session_id, date, name, description, difficulty,
        steps[i].kind, steps[i].type, steps[i].label,
        steps[i].duration_type, steps[i].duration_value,
        steps[i].target_type, steps[i].target_low, steps[i].target_high,
        steps[i].repeat_count, steps[i].parent_idx
    )
    Pour rester simple sans JS lourd cote client, on parse les champs steps[i].xxx
    via les form fields multiples.
    """
    form = await request.form()
    raw_session_id = form.get("session_id")
    athlete = _resolve_target_athlete(db, user)

    if raw_session_id:
        try:
            session_id_int = int(raw_session_id)
        except (TypeError, ValueError):
            raise HTTPException(400, "ID de séance invalide")
        s = db.get(Session, session_id_int)
        if s is None:
            raise HTTPException(404)
        # Supprimer les steps existants
        for old in list(s.steps):
            db.delete(old)
        db.flush()
    else:
        s = Session(athlete_id=athlete.id, date=date.today(), name="")

    raw_date = form.get("date")
    if not raw_date:
        raise HTTPException(400, "Date requise")
    try:
        s.date = date.fromisoformat(str(raw_date))
    except ValueError:
        raise HTTPException(400, "Date invalide (format attendu : AAAA-MM-JJ)")
    s.name = str(form.get("name") or "Seance").strip()[:100]
    s.description = (str(form.get("description") or "")).strip() or None
    try:
        s.difficulty = max(1, min(4, int(form.get("difficulty") or 1)))
    except (TypeError, ValueError):
        s.difficulty = 1

    db.add(s)
    db.flush()  # garantir s.id

    # Parse les steps
    steps_raw = _parse_steps(form)
    new_steps: list[Step] = []
    repeat_id_by_idx: dict[int, int] = {}

    for idx, raw in enumerate(steps_raw):
        kind = raw.get("kind") or "step"
        step = Step(
            session_id=s.id,
            order=idx + 1,
            kind=kind,
            label=(raw.get("label") or "").strip()[:50] or None,
            type=_safe_enum(StepType, raw.get("type"), StepType.interval),
        )

        if kind == "repeat":
            try:
                step.repeat_count = max(1, int(raw.get("repeat_count") or 1))
            except ValueError:
                step.repeat_count = 1
        else:
            step.duration_type = _safe_enum(
                DurationType, raw.get("duration_type"), DurationType.time
            )
            try:
                step.duration_value = float(raw.get("duration_value") or 0) or None
            except ValueError:
                step.duration_value = None
            # En "time", l'utilisateur entre des minutes -> convertir en secondes
            if step.duration_type == DurationType.time and step.duration_value:
                step.duration_value = step.duration_value * 60

            target_type = _safe_enum(
                TargetType, raw.get("target_type"), TargetType.no_target
            )
            step.target_type = target_type
            if target_type == TargetType.pace:
                try:
                    low = pace_min_per_km_to_m_per_s(
                        pace_str_to_min_per_km(str(raw.get("target_low") or ""))
                    )
                except (ValueError, ZeroDivisionError):
                    low = None
                try:
                    high = pace_min_per_km_to_m_per_s(
                        pace_str_to_min_per_km(str(raw.get("target_high") or ""))
                    )
                except (ValueError, ZeroDivisionError):
                    high = None
                # Si une seule allure est saisie, on la met dans low+high
                if low and not high:
                    high = low
                if high and not low:
                    low = high
                # Garmin attend low <= high (en m/s : plus lent = plus petit)
                if low and high and low > high:
                    low, high = high, low
                step.target_low = low
                step.target_high = high
            elif target_type == TargetType.heart_rate:
                try:
                    step.target_low = float(raw.get("target_low") or 0) or None
                    step.target_high = float(raw.get("target_high") or 0) or None
                except ValueError:
                    pass

            # Lien parent_repeat (optionnel) - par index
            parent_idx = raw.get("parent_idx")
            if parent_idx is not None and parent_idx != "":
                try:
                    pidx = int(parent_idx)
                    parent_id = repeat_id_by_idx.get(pidx)
                    if parent_id:
                        step.parent_repeat_id = parent_id
                except ValueError:
                    pass

        db.add(step)
        db.flush()
        new_steps.append(step)
        if kind == "repeat":
            repeat_id_by_idx[idx] = step.id

    # Recalcule duree/distance
    duration_min, distance_km = estimate_duration_and_distance(new_steps)
    s.estimated_duration_min = duration_min
    s.estimated_distance_km = distance_km

    db.add(s)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/sessions/{session_id}/delete")
async def admin_delete_session(
    session_id: int,
    user: User = Depends(require_coach),
    db: DBSession = Depends(get_session),
):
    s = db.get(Session, session_id)
    if s is None:
        raise HTTPException(404)
    db.delete(s)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


# --- Helpers ---


def _safe_enum(enum_cls, value, default):
    if value is None:
        return default
    try:
        return enum_cls(value)
    except ValueError:
        return default


def _parse_steps(form) -> list[dict]:
    """Recolte les champs `steps[i].xxx` en list[dict].

    On lit les valeurs multivaluees via getlist pour preserver les indices.
    """
    items: dict[int, dict] = {}
    for key in form.keys():
        if not key.startswith("steps["):
            continue
        try:
            after = key[len("steps[") :]
            idx_str, rest = after.split("].", 1)
            idx = int(idx_str)
        except (ValueError, IndexError):
            continue
        items.setdefault(idx, {})[rest] = form.get(key)
    return [items[i] for i in sorted(items)]
