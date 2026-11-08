"""Routes liees a Garmin : push d'une seance + onboarding du compte."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session as DBSession

from app.auth import require_user
from app.db import get_session
from app.models import Session, SessionFeedback, User
from app.services import garmin_client
from app.services.fit_export import session_to_workout_json
from app.services.workout_builder import session_to_running_workout
from app.templates import templates

router = APIRouter()


# --- Onboarding Garmin (lien du compte) ---


@router.get("/settings/garmin")
async def settings_garmin_page(
    request: Request,
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
    error: str | None = None,
    success: str | None = None,
):
    target = user
    return templates.TemplateResponse(
        request,
        "settings_garmin.html",
        {
            "user": user,
            "garmin_user": target,
            "error": error,
            "success": success,
        },
    )


@router.post("/settings/garmin")
async def settings_garmin_connect(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
):
    target = user
    try:
        result = garmin_client.login_with_credentials(email, password)
    except garmin_client.GarminMFARequired as exc:
        return templates.TemplateResponse(
            request,
            "settings_garmin.html",
            {"user": user, "garmin_user": target, "error": str(exc)},
            status_code=400,
        )
    except garmin_client.GarminError as exc:
        return templates.TemplateResponse(
            request,
            "settings_garmin.html",
            {"user": user, "garmin_user": target, "error": str(exc)},
            status_code=400,
        )

    target.garmin_token_encrypted = result.token_encrypted
    target.garmin_connected_name = result.display_name
    db.add(target)
    db.commit()
    return RedirectResponse(url="/settings/garmin?success=1", status_code=303)


@router.post("/settings/garmin/disconnect")
async def settings_garmin_disconnect(
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
):
    target = user
    target.garmin_token_encrypted = None
    target.garmin_connected_name = None
    db.add(target)
    db.commit()
    return RedirectResponse(url="/settings/garmin", status_code=303)


# --- Export d'une seance ---


@router.post("/sessions/{session_id}/export")
async def export_session(
    request: Request,
    session_id: int,
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
):
    s = db.get(Session, session_id)
    if s is None:
        raise HTTPException(404)
    if s.athlete_id != user.id:
        raise HTTPException(404)

    athlete = db.get(User, s.athlete_id)
    if athlete is None or not athlete.garmin_token_encrypted:
        return RedirectResponse(
            url=f"/sessions/{session_id}?error=garmin_not_connected",
            status_code=303,
        )

    # Si la seance n'est pas encore validee, applique l'ajustement courant
    # de l'athlete. Sinon, fige l'ajustement au moment de la validation.
    if s.feedback is None:
        adjustment_pct = athlete.pace_adjustment_pct or 0.0
    else:
        adjustment_pct = s.pace_adjustment_at_validation or 0.0

    workout = session_to_running_workout(s, pace_adjustment_pct=adjustment_pct)

    def _persist_token(new_token: bytes) -> None:
        athlete.garmin_token_encrypted = new_token
        db.add(athlete)
        db.commit()

    try:
        workout_id = garmin_client.push_running_workout(
            athlete.garmin_token_encrypted,
            workout,
            on_token_refreshed=_persist_token,
        )
    except garmin_client.GarminError as exc:
        s.last_push_error = str(exc)[:500]
        db.add(s)
        db.commit()
        return RedirectResponse(
            url=f"/sessions/{session_id}?error=push_failed",
            status_code=303,
        )

    s.garmin_workout_id = workout_id
    s.pushed_at = datetime.utcnow()
    s.last_push_error = None
    db.add(s)
    db.commit()
    return RedirectResponse(
        url=f"/sessions/{session_id}?success=exported",
        status_code=303,
    )


@router.get("/sessions/{session_id}/json")
async def export_session_json(
    session_id: int,
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
):
    """Fallback : telecharge le JSON workout (debug + future utilisation FIT)."""
    s = db.get(Session, session_id)
    if s is None:
        raise HTTPException(404)
    if s.athlete_id != user.id:
        raise HTTPException(404)

    payload = session_to_workout_json(s)
    safe_name = "".join(c for c in s.name if c.isalnum() or c in "-_") or "seance"
    return Response(
        content=payload,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.json"',
        },
    )
