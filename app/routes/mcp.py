"""Serveur MCP distant en lecture seule pour les comptes Mi Coach.

Cette première version expose le protocole MCP sur HTTP avec un bearer token
personnel généré depuis les paramètres du compte. Les outils sont strictement
limités aux données du propriétaire du token. Les écritures et OAuth 2.1
seront ajoutés dans une étape séparée, après validation de la surface de lecture.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlmodel import Session as DBSession
from sqlmodel import select

from app.db import get_session
from app.models import MCPToken, Session as TrainingSession, TrainingPlanState, User
from app.auth import require_user
from app.templates import templates

router = APIRouter()

MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_SERVER_NAME = "Mi Coach"
MCP_SERVER_VERSION = "0.1.0"
_MAX_TOKENS_PER_USER = 10
_MAX_SESSIONS = 200


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return "mcp_" + secrets.token_urlsafe(32)


def _token_from_request(request: Request, db: DBSession) -> MCPToken | None:
    """Authentifie uniquement via Authorization Bearer, jamais via le cookie web."""
    authorization = request.headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    token = value.strip()
    record = db.exec(
        select(MCPToken).where(MCPToken.token_hash == _hash_token(token))
    ).first()
    if record is None or record.revoked_at is not None:
        return None
    record.last_used_at = _utc_now()
    db.add(record)
    db.commit()
    return record


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "error": {"code": -32001, "message": "Bearer token required"},
            "id": None,
        },
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _rpc_error(request_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": request_id}
    )


def _rpc_result(request_id: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "result": result, "id": request_id})


def _tool_text(payload: Any) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            }
        ],
        "isError": False,
    }


def _date_or_none(value: Any, field_name: str) -> date | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} doit être une date ISO, par exemple 2026-11-23")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} doit être une date ISO, par exemple 2026-11-23") from exc


def _pace_targets(profile: Any) -> dict[str, Any]:
    try:
        targets = json.loads(profile.pace_targets_json or "{}")
    except (TypeError, json.JSONDecodeError):
        targets = {}
    try:
        manual = json.loads(profile.manual_paces_json or "[]")
    except (TypeError, json.JSONDecodeError):
        manual = []
    return {"calculated": targets, "manual": manual}


def _profile_payload(user: User, profile: Any, plan_state: TrainingPlanState | None) -> dict[str, Any]:
    return {
        "user": {"id": user.id, "name": user.name, "email": user.email},
        "training_profile": None
        if profile is None
        else {
            "goal": profile.goal,
            "race_date": profile.race_date,
            "experience": profile.experience,
            "current_runs_per_week": profile.current_runs_per_week,
            "longest_run_km": profile.longest_run_km,
            "sessions_per_week": profile.sessions_per_week,
            "available_days": profile.available_days,
            "ef_fast_sec_per_km": profile.ef_fast_sec_per_km,
            "ef_slow_sec_per_km": profile.ef_slow_sec_per_km,
            "pace_targets": _pace_targets(profile),
        },
        "pace_adjustment_pct": user.pace_adjustment_pct,
        "active_plan": None
        if plan_state is None
        else {
            "plan_key": plan_state.plan_key,
            "race_date": plan_state.race_date,
            "generated_at": plan_state.generated_at,
            "generated_session_count": len(_json_list(plan_state.generated_session_ids_json)),
        },
    }


def _json_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _step_payload(step: Any) -> dict[str, Any]:
    return {
        "id": step.id,
        "order": step.order,
        "kind": step.kind,
        "repeat_count": step.repeat_count,
        "type": step.type.value if hasattr(step.type, "value") else step.type,
        "label": step.label,
        "duration_type": step.duration_type.value if hasattr(step.duration_type, "value") else step.duration_type,
        "duration_value": step.duration_value,
        "target_type": step.target_type.value if hasattr(step.target_type, "value") else step.target_type,
        "target_low": step.target_low,
        "target_high": step.target_high,
        "parent_repeat_id": step.parent_repeat_id,
        "notes": step.notes,
    }


def _session_payload(session: TrainingSession, include_steps: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": session.id,
        "date": session.date,
        "name": session.name,
        "description": session.description,
        "difficulty": session.difficulty,
        "estimated_duration_min": session.estimated_duration_min,
        "estimated_distance_km": session.estimated_distance_km,
        "feedback": session.feedback.value if hasattr(session.feedback, "value") else session.feedback,
        "validated_at": session.validated_at,
        "actual_duration_sec": session.actual_duration_sec,
        "actual_distance_m": session.actual_distance_m,
        "garmin_workout_id": session.garmin_workout_id,
        "pushed_at": session.pushed_at,
    }
    if include_steps:
        payload["steps"] = [_step_payload(step) for step in session.steps]
    return payload


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "get_my_profile",
            "description": "Lire le profil running et les réglages d'allure de l'utilisateur connecté.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "get_my_training_plan",
            "description": "Lire les séances de l'utilisateur connecté, avec les étapes, le ressenti et les informations Garmin.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "from_date": {"type": "string", "format": "date", "description": "Date de début ISO facultative."},
                    "to_date": {"type": "string", "format": "date", "description": "Date de fin ISO facultative."},
                    "include_steps": {"type": "boolean", "default": True},
                },
                "additionalProperties": False,
            },
        },
    ]


def _profile_for_user(db: DBSession, user_id: int):
    from app.models import TrainingProfile

    return db.exec(select(TrainingProfile).where(TrainingProfile.user_id == user_id)).first()


def _call_tool(db: DBSession, user: User, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "get_my_profile":
        return _tool_text(
            _profile_payload(
                user,
                _profile_for_user(db, user.id),
                db.exec(select(TrainingPlanState).where(TrainingPlanState.user_id == user.id)).first(),
            )
        )

    if name == "get_my_training_plan":
        from_date = _date_or_none(arguments.get("from_date"), "from_date")
        to_date = _date_or_none(arguments.get("to_date"), "to_date")
        today = date.today()
        from_date = from_date or today - timedelta(weeks=8)
        to_date = to_date or today + timedelta(weeks=12)
        if from_date > to_date:
            raise ValueError("from_date doit être antérieure ou égale à to_date")
        include_steps = bool(arguments.get("include_steps", True))
        sessions = db.exec(
            select(TrainingSession)
            .where(TrainingSession.athlete_id == user.id)
            .where(TrainingSession.date >= from_date)
            .where(TrainingSession.date <= to_date)
            .order_by(TrainingSession.date, TrainingSession.id)
            .limit(_MAX_SESSIONS)
        ).all()
        return _tool_text(
            {
                "user_id": user.id,
                "from_date": from_date,
                "to_date": to_date,
                "truncated": len(sessions) == _MAX_SESSIONS,
                "sessions": [_session_payload(session, include_steps) for session in sessions],
            }
        )

    raise KeyError(f"Outil inconnu : {name}")


@router.post("/mcp")
async def mcp_endpoint(request: Request, db: DBSession = Depends(get_session)):
    """Endpoint MCP Streamable HTTP minimal, limité aux outils de lecture."""
    token = _token_from_request(request, db)
    if token is None:
        return _unauthorized()
    try:
        payload = await request.json()
    except Exception:
        return _rpc_error(None, -32700, "JSON invalide")
    if not isinstance(payload, dict):
        return _rpc_error(None, -32600, "Requête JSON-RPC invalide")

    request_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}
    if not isinstance(method, str) or not isinstance(params, dict):
        return _rpc_error(request_id, -32600, "Requête JSON-RPC invalide")

    user = db.get(User, token.user_id)
    if user is None:
        return _unauthorized()

    if method == "initialize":
        return _rpc_result(
            request_id,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
                "instructions": "Mi Coach expose actuellement des outils de lecture uniquement. Aucune séance ne peut être modifiée via MCP dans cette version.",
            },
        )
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return JSONResponse(status_code=202, content=None)
    if method == "ping":
        return _rpc_result(request_id, {})
    if method == "tools/list":
        return _rpc_result(request_id, {"tools": _tool_definitions()})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return _rpc_error(request_id, -32602, "Paramètres tools/call invalides")
        try:
            return _rpc_result(request_id, _call_tool(db, user, name, arguments))
        except (KeyError, ValueError) as exc:
            return _rpc_error(request_id, -32602, str(exc))

    return _rpc_error(request_id, -32601, f"Méthode MCP non supportée : {method}")


@router.get("/settings/mcp")
async def mcp_settings_page(
    request: Request,
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
):
    request.state.user = user
    tokens = db.exec(
        select(MCPToken)
        .where(MCPToken.user_id == user.id)
        .where(MCPToken.revoked_at.is_(None))
        .order_by(MCPToken.created_at.desc())
    ).all()
    new_token = request.session.pop("mcp_new_token", None)
    return templates.TemplateResponse(
        request,
        "settings_mcp.html",
        {"user": user, "tokens": tokens, "new_token": new_token, "error": None},
    )


@router.post("/settings/mcp/tokens")
async def create_mcp_token(
    request: Request,
    label: str = Form("Assistant IA"),
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
):
    active_count = len(
        db.exec(
            select(MCPToken)
            .where(MCPToken.user_id == user.id)
            .where(MCPToken.revoked_at.is_(None))
        ).all()
    )
    if active_count >= _MAX_TOKENS_PER_USER:
        return RedirectResponse(url="/settings/mcp?error=limit", status_code=303)
    raw_token = _new_token()
    clean_label = (label or "Assistant IA").strip()[:80] or "Assistant IA"
    db.add(
        MCPToken(
            user_id=user.id,
            token_hash=_hash_token(raw_token),
            token_prefix=raw_token[:12],
            label=clean_label,
        )
    )
    db.commit()
    request.session["mcp_new_token"] = raw_token
    return RedirectResponse(url="/settings/mcp?created=1", status_code=303)


@router.post("/settings/mcp/tokens/{token_id}/revoke")
async def revoke_mcp_token(
    token_id: int,
    user: User = Depends(require_user),
    db: DBSession = Depends(get_session),
):
    token = db.get(MCPToken, token_id)
    if token is not None and token.user_id == user.id and token.revoked_at is None:
        token.revoked_at = _utc_now()
        db.add(token)
        db.commit()
    return RedirectResponse(url="/settings/mcp?revoked=1", status_code=303)
