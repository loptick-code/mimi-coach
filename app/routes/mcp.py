"""Serveur MCP distant et autorisation OAuth pour Mi Coach.

Le serveur est stateless côté MCP. Les tokens manuels restent disponibles pour
les premiers tests de lecture, tandis que les clients MCP compatibles utilisent
OAuth 2.1 + PKCE. Les écritures passent toujours par une proposition stockée,
un aperçu et une confirmation séparée.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlmodel import Session as DBSession
from sqlmodel import select

from app.auth import require_user
from app.db import get_session
from app.models import (
    MCPAuthorizationCode,
    MCPChangeProposal,
    MCPClient,
    MCPToken,
    Session as TrainingSession,
    TrainingPlanState,
    User,
)
from app.services.program_import import (
    MAX_PROGRAM_CHARS,
    ProgramImportError,
    apply_program_import,
    build_program_import_plan,
    preview_rows,
)
from app.templates import templates

router = APIRouter()

MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_SERVER_NAME = "Mi Coach"
MCP_SERVER_VERSION = "0.3.0"
MCP_SCOPES = {"profile:read", "plan:read", "activity:read", "plan:write"}
MCP_READ_SCOPES = {"profile:read", "plan:read", "activity:read"}
_MAX_TOKENS_PER_USER = 10
_MAX_SESSIONS = 200
_AUTH_CODE_TTL = timedelta(minutes=5)
_ACCESS_TOKEN_TTL = timedelta(hours=1)
_PROPOSAL_TTL = timedelta(minutes=10)


@dataclass(frozen=True)
class MCPAuthContext:
    user_id: int
    scopes: frozenset[str]
    token_id: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return "mcp_" + secrets.token_urlsafe(32)


def _new_identifier(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(18)


def _base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host", request.url.netloc).split(",")[0].strip()
    return f"{proto}://{host}".rstrip("/")


def _origin_is_allowed(request: Request) -> bool:
    """Bloque les requêtes navigateur cross-origin non autorisées."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    return origin.rstrip("/") == _base_url(request)


def _mcp_headers() -> dict[str, str]:
    return {"MCP-Protocol-Version": MCP_PROTOCOL_VERSION}


def _token_from_request(request: Request, db: DBSession) -> MCPAuthContext | None:
    """Authentifie via bearer token, jamais via le cookie web."""
    authorization = request.headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    record = db.exec(
        select(MCPToken).where(MCPToken.token_hash == _hash_token(value.strip()))
    ).first()
    if record is None or record.revoked_at is not None:
        return None
    now = _utc_now()
    if record.expires_at is not None and record.expires_at <= now:
        return None
    record.last_used_at = now
    db.add(record)
    db.commit()
    return MCPAuthContext(
        user_id=record.user_id,
        scopes=frozenset((record.scope or "").split()),
        token_id=record.id,
    )


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "error": {"code": -32001, "message": "Bearer token required"},
            "id": None,
        },
        status_code=401,
        headers={"WWW-Authenticate": "Bearer", **_mcp_headers()},
    )


def _rpc_error(request_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": request_id},
        headers=_mcp_headers(),
    )


def _rpc_result(request_id: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "result": result, "id": request_id},
        headers=_mcp_headers(),
    )


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


def _json_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


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


def _profile_for_user(db: DBSession, user_id: int):
    from app.models import TrainingProfile

    return db.exec(select(TrainingProfile).where(TrainingProfile.user_id == user_id)).first()


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


def _fingerprint_plan(db: DBSession, user_id: int) -> str:
    sessions = db.exec(
        select(TrainingSession)
        .where(TrainingSession.athlete_id == user_id)
        .order_by(TrainingSession.id)
    ).all()
    value = [
        {
            "id": session.id,
            "updated_at": session.updated_at.isoformat() if session.updated_at else None,
            "validated_at": session.validated_at.isoformat() if session.validated_at else None,
        }
        for session in sessions
    ]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def _read_annotations() -> dict[str, bool]:
    return {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }


def _preview_annotations() -> dict[str, bool]:
    return {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }


def _write_annotations() -> dict[str, bool]:
    return {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    }


def _tool_definitions(scopes: frozenset[str]) -> list[dict[str, Any]]:
    tools = [
        {
            "name": "get_my_profile",
            "description": "Lire le profil running et les réglages d'allure de l'utilisateur connecté.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": _read_annotations(),
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
            "annotations": _read_annotations(),
        },
        {
            "name": "get_my_recent_activities",
            "description": "Lire les séances récentes déjà réalisées, leurs ressentis et les données réellement enregistrées.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "weeks": {"type": "integer", "minimum": 1, "maximum": 24, "default": 8},
                    "include_steps": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
            "annotations": _read_annotations(),
        },
    ]
    if "plan:write" in scopes:
        tools.extend(
            [
                {
                    "name": "preview_program_change",
                    "description": "Valider un fichier Markdown Mi Coach, calculer le diff et créer une proposition sans modifier le programme.",
                    "annotations": _preview_annotations(),
                    "inputSchema": {
                        "type": "object",
                        "required": ["markdown"],
                        "properties": {
                            "markdown": {"type": "string", "maxLength": MAX_PROGRAM_CHARS},
                            "overwrite_existing": {"type": "boolean", "default": False},
                        },
                        "additionalProperties": False,
                    },
                },
                {
                    "name": "apply_program_change",
                    "description": "Appliquer une proposition de programme après confirmation explicite de l'utilisateur.",
                    "annotations": _write_annotations(),
                    "inputSchema": {
                        "type": "object",
                        "required": ["proposal_id", "confirm"],
                        "properties": {
                            "proposal_id": {"type": "string"},
                            "confirm": {"type": "boolean"},
                        },
                        "additionalProperties": False,
                    },
                },
            ]
        )
    return tools


def _required_scope(tool_name: str) -> str:
    if tool_name == "get_my_profile":
        return "profile:read"
    if tool_name == "get_my_training_plan":
        return "plan:read"
    if tool_name == "get_my_recent_activities":
        return "activity:read"
    return "plan:write"


def _call_tool(
    db: DBSession,
    user: User,
    auth: MCPAuthContext,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    required_scope = _required_scope(name)
    if required_scope not in auth.scopes:
        raise PermissionError(f"Scope requis : {required_scope}")

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
            .limit(_MAX_SESSIONS + 1)
        ).all()
        truncated = len(sessions) > _MAX_SESSIONS
        return _tool_text(
            {
                "user_id": user.id,
                "from_date": from_date,
                "to_date": to_date,
                "truncated": truncated,
                "sessions": [_session_payload(session, include_steps) for session in sessions[:_MAX_SESSIONS]],
            }
        )

    if name == "get_my_recent_activities":
        weeks = arguments.get("weeks", 8)
        if not isinstance(weeks, int) or isinstance(weeks, bool) or not 1 <= weeks <= 24:
            raise ValueError("weeks doit être un entier compris entre 1 et 24")
        include_steps = bool(arguments.get("include_steps", False))
        today = date.today()
        from_date = today - timedelta(weeks=weeks)
        sessions = db.exec(
            select(TrainingSession)
            .where(TrainingSession.athlete_id == user.id)
            .where(TrainingSession.date >= from_date)
            .where(TrainingSession.date <= today)
            .order_by(TrainingSession.date.desc(), TrainingSession.id.desc())
            .limit(_MAX_SESSIONS)
        ).all()
        return _tool_text(
            {
                "user_id": user.id,
                "from_date": from_date,
                "to_date": today,
                "sessions": [_session_payload(session, include_steps) for session in sessions],
            }
        )

    if name == "preview_program_change":
        markdown = arguments.get("markdown")
        if not isinstance(markdown, str):
            raise ValueError("markdown doit être une chaîne de caractères")
        profile = _profile_for_user(db, user.id)
        if profile is None:
            raise ValueError("Le profil running doit être créé avant toute modification.")
        plan = build_program_import_plan(
            db,
            user,
            profile,
            markdown,
            overwrite_existing=bool(arguments.get("overwrite_existing", False)),
        )
        proposal_id = _new_identifier("prop_")
        expires_at = _utc_now() + _PROPOSAL_TTL
        payload = {
            "markdown": markdown,
            "allow_existing": bool(arguments.get("overwrite_existing", False)),
        }
        preview = {
            "proposal_id": proposal_id,
            "expires_at": expires_at,
            "start_date": plan.start_date,
            "week_numbers": plan.week_numbers,
            "session_count": len(plan.workouts),
            "existing_weeks": {
                str(week): len(sessions) for week, sessions in plan.existing_weeks.items()
            },
            "protected_session_count": len(plan.protected_sessions),
            "overwrite_existing": bool(arguments.get("overwrite_existing", False)),
            "sessions": preview_rows(plan),
        }
        db.add(
            MCPChangeProposal(
                proposal_id=proposal_id,
                user_id=user.id,
                operation="program_import",
                payload_json=json.dumps(payload, ensure_ascii=False, default=str),
                preview_json=json.dumps(preview, ensure_ascii=False, default=str),
                current_fingerprint=_fingerprint_plan(db, user.id),
                expires_at=expires_at,
            )
        )
        db.commit()
        if plan.existing_weeks and not payload["allow_existing"]:
            preview["can_apply"] = False
            preview["warning"] = "Des semaines existent déjà. Refais un aperçu avec overwrite_existing=true pour autoriser leur remplacement."
        elif plan.protected_sessions:
            preview["can_apply"] = False
            preview["warning"] = "Le programme touche des séances passées ou validées, qui restent protégées."
        else:
            preview["can_apply"] = True
        return _tool_text(preview)

    if name == "apply_program_change":
        proposal_id = arguments.get("proposal_id")
        if not isinstance(proposal_id, str):
            raise ValueError("proposal_id est obligatoire")
        if arguments.get("confirm") is not True:
            raise ValueError("confirm=true est obligatoire pour appliquer une proposition")
        proposal = db.exec(
            select(MCPChangeProposal)
            .where(MCPChangeProposal.proposal_id == proposal_id)
            .where(MCPChangeProposal.user_id == user.id)
        ).first()
        if proposal is None:
            raise ValueError("Proposition inconnue ou non accessible")
        now = _utc_now()
        if proposal.applied_at is not None:
            raise ValueError("Cette proposition a déjà été appliquée")
        if proposal.expires_at <= now:
            raise ValueError("Cette proposition a expiré. Crée un nouvel aperçu")
        if proposal.current_fingerprint != _fingerprint_plan(db, user.id):
            raise ValueError("Le programme a changé depuis l'aperçu. Crée une nouvelle proposition")
        payload = json.loads(proposal.payload_json)
        profile = _profile_for_user(db, user.id)
        if profile is None:
            raise ValueError("Le profil running doit être créé avant toute modification.")
        plan = build_program_import_plan(db, user, profile, payload["markdown"], overwrite_existing=True)
        try:
            created = apply_program_import(
                db,
                user,
                profile,
                plan,
                allow_existing=bool(payload.get("allow_existing", False)),
            )
        except ProgramImportError as exc:
            raise ValueError(str(exc)) from exc
        proposal.applied_at = now
        db.add(proposal)
        db.commit()
        return _tool_text(
            {
                "applied": True,
                "proposal_id": proposal_id,
                "created_sessions": [
                    {"id": session.id, "date": session.date, "name": session.name}
                    for session in created
                ],
            }
        )

    raise KeyError(f"Outil inconnu : {name}")


@router.post("/mcp")
async def mcp_endpoint(request: Request, db: DBSession = Depends(get_session)):
    """Endpoint MCP Streamable HTTP stateless."""
    if not _origin_is_allowed(request):
        return JSONResponse({"error": "Origin not allowed"}, status_code=403, headers=_mcp_headers())
    auth = _token_from_request(request, db)
    if auth is None:
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
    user = db.get(User, auth.user_id)
    if user is None:
        return _unauthorized()

    if method == "initialize":
        return _rpc_result(
            request_id,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
                "instructions": "Les écritures passent par un aperçu puis une confirmation explicite.",
            },
        )
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return Response(status_code=202, headers=_mcp_headers())
    if method == "ping":
        return _rpc_result(request_id, {})
    if method == "tools/list":
        return _rpc_result(request_id, {"tools": _tool_definitions(auth.scopes)})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return _rpc_error(request_id, -32602, "Paramètres tools/call invalides")
        try:
            return _rpc_result(request_id, _call_tool(db, user, auth, name, arguments))
        except PermissionError as exc:
            return _rpc_error(request_id, -32003, str(exc))
        except (KeyError, ValueError, ProgramImportError) as exc:
            return _rpc_error(request_id, -32602, str(exc))

    return _rpc_error(request_id, -32601, f"Méthode MCP non supportée : {method}")


# ---------------------------------------------------------------------------
# OAuth 2.1 + PKCE
# ---------------------------------------------------------------------------


def _oauth_metadata(request: Request) -> dict[str, Any]:
    base = _base_url(request)
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": sorted(MCP_SCOPES),
    }


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
async def oauth_protected_resource_metadata(request: Request):
    base = _base_url(request)
    return {
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
        "scopes_supported": sorted(MCP_SCOPES),
    }


@router.get("/.well-known/oauth-authorization-server")
async def oauth_authorization_server_metadata(request: Request):
    return _oauth_metadata(request)


def _valid_redirect_uri(uri: str) -> bool:
    parsed = urlsplit(uri)
    if parsed.scheme == "https" and parsed.netloc:
        return True
    return parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}


@router.post("/oauth/register")
async def oauth_register(request: Request, db: DBSession = Depends(get_session)):
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(400, "JSON invalide") from exc
    client_name = str(payload.get("client_name") or "Assistant MCP").strip()[:120]
    redirect_uris = payload.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris or any(
        not isinstance(uri, str) or not _valid_redirect_uri(uri) for uri in redirect_uris
    ):
        raise HTTPException(400, "redirect_uris doit contenir des URL HTTPS ou localhost valides")
    client_id = _new_identifier("client_")
    client = MCPClient(
        client_id=client_id,
        client_name=client_name,
        redirect_uris_json=json.dumps(sorted(set(redirect_uris)), ensure_ascii=False),
    )
    db.add(client)
    db.commit()
    return {
        "client_id": client_id,
        "client_name": client_name,
        "redirect_uris": json.loads(client.redirect_uris_json),
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
    }


def _client(db: DBSession, client_id: str) -> MCPClient:
    client = db.exec(select(MCPClient).where(MCPClient.client_id == client_id)).first()
    if client is None:
        raise HTTPException(400, "client_id inconnu")
    return client


def _authorization_params(request: Request, db: DBSession) -> tuple[MCPClient, dict[str, str], str]:
    query = request.query_params
    required = ["client_id", "redirect_uri", "response_type", "code_challenge", "code_challenge_method"]
    if any(not query.get(name) for name in required):
        raise HTTPException(400, "Paramètres OAuth incomplets")
    client = _client(db, query["client_id"])
    redirect_uri = query["redirect_uri"]
    redirect_uris = _json_list(client.redirect_uris_json)
    if redirect_uri not in redirect_uris:
        raise HTTPException(400, "redirect_uri non enregistré")
    if query["response_type"] != "code" or query["code_challenge_method"] != "S256":
        raise HTTPException(400, "OAuth exige response_type=code et PKCE S256")
    resource = query.get("resource")
    if resource and resource.rstrip("/") != f"{_base_url(request)}/mcp":
        raise HTTPException(400, "resource OAuth invalide")
    requested = set((query.get("scope") or "").split())
    if not requested:
        requested = set(MCP_READ_SCOPES)
    if not requested.issubset(MCP_SCOPES):
        raise HTTPException(400, "Scope OAuth inconnu")
    params = {
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(sorted(requested)),
        "code_challenge": query["code_challenge"],
        "code_challenge_method": "S256",
        "state": query.get("state") or "",
    }
    return client, params, query.get("resource") or ""


def _oauth_redirect(uri: str, **values: str) -> RedirectResponse:
    parsed = urlsplit(uri)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({key: value for key, value in values.items() if value != ""})
    destination = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    return RedirectResponse(destination, status_code=303)


@router.get("/oauth/authorize")
async def oauth_authorize_page(
    request: Request,
    db: DBSession = Depends(get_session),
):
    client, params, _resource = _authorization_params(request, db)
    user = request.session.get("user_id")
    if not user:
        return_to = request.url.path
        if request.url.query:
            return_to += "?" + request.url.query
        request.session["mcp_oauth_return_to"] = return_to
        return RedirectResponse("/login", status_code=303)
    csrf = secrets.token_urlsafe(24)
    request.session["mcp_oauth_csrf"] = csrf
    return templates.TemplateResponse(
        request,
        "oauth_consent.html",
        {
            "user": db.get(User, user),
            "client": client,
            "params": params,
            "csrf": csrf,
            "scope_labels": {
                "profile:read": "Lire mon profil et mes allures",
                "plan:read": "Lire mon programme et mes séances",
                "activity:read": "Lire mes ressentis et activités",
                "plan:write": "Proposer et appliquer des changements de programme",
            },
        },
    )


@router.post("/oauth/authorize")
async def oauth_authorize_decision(
    request: Request,
    csrf: str = Form(...),
    decision: str = Form(...),
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    scope: str = Form(...),
    code_challenge: str = Form(...),
    code_challenge_method: str = Form(...),
    state: str = Form(""),
    db: DBSession = Depends(get_session),
):
    if request.session.pop("mcp_oauth_csrf", None) != csrf:
        raise HTTPException(400, "Demande OAuth expirée")
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(401, "Connexion Mi Coach requise")
    client = _client(db, client_id)
    if redirect_uri not in _json_list(client.redirect_uris_json):
        raise HTTPException(400, "redirect_uri non enregistré")
    if code_challenge_method != "S256":
        raise HTTPException(400, "PKCE S256 obligatoire")
    requested_scopes = set(scope.split())
    if not requested_scopes or not requested_scopes.issubset(MCP_SCOPES):
        raise HTTPException(400, "Scope OAuth inconnu")
    if decision != "approve":
        return _oauth_redirect(redirect_uri, error="access_denied", state=state)
    raw_code = _new_identifier("code_")
    db.add(
        MCPAuthorizationCode(
            code_hash=_hash_token(raw_code),
            client_id=client_id,
            user_id=user_id,
            redirect_uri=redirect_uri,
            scope=scope,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            expires_at=_utc_now() + _AUTH_CODE_TTL,
        )
    )
    db.commit()
    return _oauth_redirect(redirect_uri, code=raw_code, state=state)


@router.post("/oauth/token")
async def oauth_token(
    grant_type: str = Form(...),
    code: str = Form(...),
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    code_verifier: str = Form(...),
    db: DBSession = Depends(get_session),
):
    if grant_type != "authorization_code":
        raise HTTPException(400, "grant_type non supporté")
    auth_code = db.exec(
        select(MCPAuthorizationCode)
        .where(MCPAuthorizationCode.code_hash == _hash_token(code))
        .where(MCPAuthorizationCode.client_id == client_id)
        .where(MCPAuthorizationCode.redirect_uri == redirect_uri)
    ).first()
    now = _utc_now()
    if auth_code is None or auth_code.consumed_at is not None or auth_code.expires_at <= now:
        raise HTTPException(400, "code OAuth invalide ou expiré")
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    if not secrets.compare_digest(expected, auth_code.code_challenge):
        raise HTTPException(400, "PKCE invalide")
    auth_code.consumed_at = now
    raw_access_token = _new_token()
    token = MCPToken(
        user_id=auth_code.user_id,
        token_hash=_hash_token(raw_access_token),
        token_prefix=raw_access_token[:12],
        label=f"OAuth · {client_id[:24]}",
        scope=auth_code.scope,
        client_id=client_id,
        expires_at=now + _ACCESS_TOKEN_TTL,
    )
    db.add(auth_code)
    db.add(token)
    db.commit()
    return {
        "access_token": raw_access_token,
        "token_type": "Bearer",
        "expires_in": int(_ACCESS_TOKEN_TTL.total_seconds()),
        "scope": auth_code.scope,
    }


# ---------------------------------------------------------------------------
# Gestion manuelle des tokens et consentements
# ---------------------------------------------------------------------------


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
    return templates.TemplateResponse(
        request,
        "settings_mcp.html",
        {"user": user, "tokens": tokens, "new_token": None, "error": None},
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
            scope="profile:read plan:read activity:read",
        )
    )
    db.commit()
    tokens = db.exec(
        select(MCPToken)
        .where(MCPToken.user_id == user.id)
        .where(MCPToken.revoked_at.is_(None))
        .order_by(MCPToken.created_at.desc())
    ).all()
    request.state.user = user
    return templates.TemplateResponse(
        request,
        "settings_mcp.html",
        {"user": user, "tokens": tokens, "new_token": raw_token, "error": None},
    )


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
