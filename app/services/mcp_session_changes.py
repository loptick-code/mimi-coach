"""Safe, previewable MCP operations on individual training sessions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlmodel import Session as DBSession
from sqlmodel import delete, select, update

from app.models import (
    DurationType,
    Session,
    SessionFeedback,
    Step,
    StepType,
    TargetType,
    TrainingPlanState,
    User,
)
from app.services.session_cleanup import delete_sessions_safely
from app.services.workout_builder import estimate_duration_and_distance

MAX_OPERATIONS = 20
MAX_STEPS_PER_SESSION = 60


class SessionChangeError(ValueError):
    pass


@dataclass
class SessionChangePlan:
    operations: list[dict[str, Any]]
    preview: dict[str, Any]


def _parse_date(value: Any, field: str = "date") -> date:
    if not isinstance(value, str):
        raise SessionChangeError(f"{field} doit être une date ISO, par exemple 2026-12-05")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SessionChangeError(f"{field} doit être une date ISO, par exemple 2026-12-05") from exc


def _editable_session(db: DBSession, user: User, session_id: Any) -> Session:
    if not isinstance(session_id, int) or isinstance(session_id, bool):
        raise SessionChangeError("session_id doit être un entier")
    session = db.exec(
        select(Session).where(Session.id == session_id).where(Session.athlete_id == user.id)
    ).first()
    if session is None:
        raise SessionChangeError(f"Séance introuvable : {session_id}")
    if session.date < date.today():
        raise SessionChangeError(f"La séance {session_id} est passée et reste protégée")
    if session.validated_at is not None or session.feedback is not None:
        raise SessionChangeError(f"La séance {session_id} est validée et reste protégée")
    if session.pushed_at is not None or session.garmin_workout_id is not None:
        raise SessionChangeError(f"La séance {session_id} a été envoyée à Garmin et reste protégée")
    if session.garmin_activity_id is not None or session.actual_duration_sec is not None:
        raise SessionChangeError(f"La séance {session_id} contient une activité réelle et reste protégée")
    return session


def _validate_step(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SessionChangeError(f"steps[{index}] doit être un objet")
    kind = raw.get("kind", "step")
    if kind not in {"step", "repeat"}:
        raise SessionChangeError(f"steps[{index}].kind invalide")
    step_type = raw.get("type", "other")
    if step_type not in {item.value for item in StepType}:
        raise SessionChangeError(f"steps[{index}].type invalide")
    duration_type = raw.get("duration_type", "time")
    if duration_type not in {item.value for item in DurationType}:
        raise SessionChangeError(f"steps[{index}].duration_type invalide")
    target_type = raw.get("target_type", "no_target")
    if target_type not in {item.value for item in TargetType}:
        raise SessionChangeError(f"steps[{index}].target_type invalide")
    duration_value = raw.get("duration_value")
    if duration_value is not None and (not isinstance(duration_value, (int, float)) or duration_value < 0):
        raise SessionChangeError(f"steps[{index}].duration_value invalide")
    repeat_count = raw.get("repeat_count")
    if kind == "repeat":
        if not isinstance(repeat_count, int) or isinstance(repeat_count, bool) or not 2 <= repeat_count <= 100:
            raise SessionChangeError(f"steps[{index}].repeat_count doit être compris entre 2 et 100")
    elif repeat_count is not None:
        raise SessionChangeError(f"steps[{index}] ne peut pas avoir repeat_count")
    parent_index = raw.get("parent_repeat_index")
    if parent_index is not None and (not isinstance(parent_index, int) or parent_index < 0 or parent_index >= index):
        raise SessionChangeError(f"steps[{index}].parent_repeat_index invalide")
    result = {
        "kind": kind,
        "type": step_type,
        "label": str(raw.get("label") or "")[:120] or None,
        "duration_type": duration_type,
        "duration_value": duration_value,
        "target_type": target_type,
        "target_low": raw.get("target_low"),
        "target_high": raw.get("target_high"),
        "notes": str(raw.get("notes") or "")[:500] or None,
        "repeat_count": repeat_count,
        "parent_repeat_index": parent_index,
    }
    for field in ("target_low", "target_high"):
        if result[field] is not None and (not isinstance(result[field], (int, float)) or result[field] < 0):
            raise SessionChangeError(f"steps[{index}].{field} invalide")
    if target_type == "no_target" and (result["target_low"] is not None or result["target_high"] is not None):
        raise SessionChangeError(f"steps[{index}] ne peut pas avoir de cibles avec target_type=no_target")
    return result


def _validate_steps(raw_steps: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_steps, list) or len(raw_steps) > MAX_STEPS_PER_SESSION:
        raise SessionChangeError(f"steps doit être une liste de 0 à {MAX_STEPS_PER_SESSION} étapes")
    steps = [_validate_step(raw, index) for index, raw in enumerate(raw_steps)]
    for index, step in enumerate(steps):
        if step["parent_repeat_index"] is not None:
            parent = steps[step["parent_repeat_index"]]
            if parent["kind"] != "repeat":
                raise SessionChangeError(f"steps[{index}] doit référencer un groupe repeat")
    return steps


def _validate_pace_updates(db: DBSession, session: Session, raw_updates: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_updates, list) or len(raw_updates) > MAX_STEPS_PER_SESSION:
        raise SessionChangeError("pace_updates doit être une liste valide")
    step_ids = {step.id for step in session.steps}
    updates: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_updates):
        if not isinstance(raw, dict) or not isinstance(raw.get("step_id"), int):
            raise SessionChangeError(f"pace_updates[{index}] est invalide")
        if raw["step_id"] not in step_ids:
            raise SessionChangeError(f"L'étape {raw['step_id']} n'appartient pas à la séance {session.id}")
        low, high = raw.get("target_low"), raw.get("target_high")
        if not isinstance(low, (int, float)) or not isinstance(high, (int, float)) or low < 0 or high < 0:
            raise SessionChangeError(f"pace_updates[{index}] doit contenir target_low et target_high")
        updates.append({"step_id": raw["step_id"], "target_low": low, "target_high": high})
    return updates


def _session_snapshot(session: Session) -> dict[str, Any]:
    return {
        "id": session.id,
        "date": session.date,
        "name": session.name,
        "description": session.description,
        "difficulty": session.difficulty,
        "estimated_duration_min": session.estimated_duration_min,
        "estimated_distance_km": session.estimated_distance_km,
        "steps": [
            {
                "id": step.id,
                "order": step.order,
                "kind": step.kind,
                "type": step.type.value,
                "label": step.label,
                "duration_type": step.duration_type.value,
                "duration_value": step.duration_value,
                "target_type": step.target_type.value,
                "target_low": step.target_low,
                "target_high": step.target_high,
                "repeat_count": step.repeat_count,
                "parent_repeat_id": step.parent_repeat_id,
            }
            for step in session.steps
        ],
    }


def _new_session_snapshot(operation: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": None,
        "date": operation["date"],
        "name": operation["name"],
        "description": operation.get("description"),
        "difficulty": operation.get("difficulty", 1),
        "steps": operation.get("steps", []),
    }


def build_change_plan(db: DBSession, user: User, operations: Any) -> SessionChangePlan:
    if not isinstance(operations, list) or not operations or len(operations) > MAX_OPERATIONS:
        raise SessionChangeError(f"operations doit contenir entre 1 et {MAX_OPERATIONS} opérations")
    normalized: list[dict[str, Any]] = []
    preview_operations: list[dict[str, Any]] = []
    touched_ids: set[int] = set()
    for index, raw in enumerate(operations):
        if not isinstance(raw, dict):
            raise SessionChangeError(f"operations[{index}] doit être un objet")
        action = raw.get("action")
        if action not in {"create", "update", "move", "delete"}:
            raise SessionChangeError(f"operations[{index}].action doit être create, update, move ou delete")
        if action == "create":
            if "date" not in raw or not raw.get("name"):
                raise SessionChangeError(f"operations[{index}] create exige date et name")
            operation = {
                "action": "create",
                "date": _parse_date(raw["date"]),
                "name": str(raw["name"])[:100],
                "description": str(raw.get("description") or "")[:2000] or None,
                "difficulty": int(raw.get("difficulty", 1)),
                "steps": _validate_steps(raw.get("steps", [])),
            }
            if not 1 <= operation["difficulty"] <= 4:
                raise SessionChangeError("difficulty doit être compris entre 1 et 4")
            if operation["date"] < date.today():
                raise SessionChangeError("Une séance ne peut pas être créée dans le passé")
            normalized.append(operation)
            preview_operations.append({"action": "create", "before": None, "after": _new_session_snapshot(operation)})
            continue

        session = _editable_session(db, user, raw.get("session_id"))
        if session.id in touched_ids:
            raise SessionChangeError(f"La séance {session.id} apparaît plusieurs fois")
        touched_ids.add(session.id)
        before = _session_snapshot(session)
        if action == "delete":
            normalized.append({"action": "delete", "session_id": session.id})
            preview_operations.append({"action": action, "before": before, "after": None})
            continue

        operation = {"action": action, "session_id": session.id}
        if action == "move":
            if "date" not in raw:
                raise SessionChangeError("move exige date")
            operation["date"] = _parse_date(raw["date"])
        else:
            allowed = {"date", "name", "description", "difficulty", "steps", "pace_updates"}
            unknown = set(raw) - allowed - {"action", "session_id"}
            if unknown:
                raise SessionChangeError(f"Champs update inconnus : {', '.join(sorted(unknown))}")
            if not any(field in raw for field in allowed):
                raise SessionChangeError("update doit contenir au moins une modification")
            if "date" in raw:
                operation["date"] = _parse_date(raw["date"])
            if "name" in raw:
                operation["name"] = str(raw["name"])[:100]
            if "description" in raw:
                operation["description"] = str(raw["description"])[:2000]
            if "difficulty" in raw:
                operation["difficulty"] = int(raw["difficulty"])
                if not 1 <= operation["difficulty"] <= 4:
                    raise SessionChangeError("difficulty doit être compris entre 1 et 4")
            if "steps" in raw:
                operation["steps"] = _validate_steps(raw["steps"])
            if "pace_updates" in raw:
                operation["pace_updates"] = _validate_pace_updates(db, session, raw["pace_updates"])
        if operation.get("date", session.date) < date.today():
            raise SessionChangeError("Une séance modifiée ne peut pas être déplacée dans le passé")
        after = dict(before)
        for field in ("date", "name", "description", "difficulty"):
            if field in operation:
                after[field] = operation[field]
        if "steps" in operation:
            after["steps"] = operation["steps"]
        if "pace_updates" in operation:
            for pace_update in operation["pace_updates"]:
                for step in after["steps"]:
                    if step.get("id") == pace_update["step_id"]:
                        step.update({"target_low": pace_update["target_low"], "target_high": pace_update["target_high"]})
        normalized.append(operation)
        preview_operations.append({"action": action, "before": before, "after": after})

    return SessionChangePlan(
        operations=normalized,
        preview={"operation_count": len(normalized), "operations": preview_operations},
    )


def _replace_steps(db: DBSession, session: Session, steps: list[dict[str, Any]]) -> list[Step]:
    existing_ids = [step.id for step in session.steps if step.id is not None]
    if existing_ids:
        db.exec(update(Step).where(Step.id.in_(existing_ids)).values(parent_repeat_id=None))
        db.exec(delete(Step).where(Step.id.in_(existing_ids)))
        db.flush()
    created: list[Step] = []
    parent_ids: dict[int, int] = {}
    for index, raw in enumerate(steps):
        step = Step(
            session_id=session.id,
            order=index + 1,
            parent_repeat_id=parent_ids.get(raw["parent_repeat_index"]),
            kind=raw["kind"],
            repeat_count=raw["repeat_count"],
            type=StepType(raw["type"]),
            label=raw["label"],
            duration_type=DurationType(raw["duration_type"]),
            duration_value=raw["duration_value"],
            target_type=TargetType(raw["target_type"]),
            target_low=raw["target_low"],
            target_high=raw["target_high"],
            notes=raw["notes"],
        )
        db.add(step)
        db.flush()
        created.append(step)
        if raw["kind"] == "repeat":
            parent_ids[index] = step.id
    return created


def apply_change_plan(db: DBSession, user: User, plan: SessionChangePlan) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    deleted_ids: set[int] = set()
    for operation in plan.operations:
        action = operation["action"]
        if action == "create":
            session = Session(
                athlete_id=user.id,
                date=operation["date"],
                name=operation["name"],
                description=operation["description"] or "Créée via un assistant MCP.",
                difficulty=operation["difficulty"],
            )
            db.add(session)
            db.flush()
            steps = _replace_steps(db, session, operation["steps"])
            session.estimated_duration_min, session.estimated_distance_km = estimate_duration_and_distance(steps)
            db.add(session)
            created.append({"id": session.id, "date": session.date, "name": session.name})
            continue

        session = _editable_session(db, user, operation["session_id"])
        if action == "delete":
            deleted_ids.add(session.id)
            delete_sessions_safely(db, [session])
            continue
        if "date" in operation:
            session.date = operation["date"]
        if "name" in operation:
            session.name = operation["name"]
        if "description" in operation:
            session.description = operation["description"]
        if "difficulty" in operation:
            session.difficulty = operation["difficulty"]
        if "steps" in operation:
            steps = _replace_steps(db, session, operation["steps"])
            session.estimated_duration_min, session.estimated_distance_km = estimate_duration_and_distance(steps)
        if "pace_updates" in operation:
            step_ids = {item["step_id"] for item in operation["pace_updates"]}
            for step in db.exec(select(Step).where(Step.id.in_(step_ids))).all():
                update_item = next(item for item in operation["pace_updates"] if item["step_id"] == step.id)
                step.target_type = TargetType.pace
                step.target_low = update_item["target_low"]
                step.target_high = update_item["target_high"]
                db.add(step)
        session.updated_at = datetime.now(timezone.utc)
        db.add(session)

    state = db.exec(select(TrainingPlanState).where(TrainingPlanState.user_id == user.id)).first()
    if state is not None and deleted_ids:
        current_ids = [item for item in json.loads(state.generated_session_ids_json or "[]") if int(item) not in deleted_ids]
        state.generated_session_ids_json = json.dumps(current_ids)
        db.add(state)
    db.commit()
    return created
