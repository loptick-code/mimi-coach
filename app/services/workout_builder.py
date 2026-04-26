"""Conversion d'une Session DB en RunningWorkout Garmin.

Le format Garmin Connect attend :
- Un RunningWorkout avec un nom, une description, des segments
- Un segment running unique contenant des steps lineaires
- Les steps peuvent etre soit des ExecutableStep, soit des RepeatGroup
  (qui contiennent leurs propres ExecutableStep)

Mapping de notre modele DB :
- Step.kind == "step"   -> ExecutableStep avec stepType warmup/interval/recovery/cooldown
- Step.kind == "repeat" -> RepeatGroup englobant les enfants (steps avec parent_repeat_id)

Targets :
- pace : convertis min/km en m/s (Garmin attend des m/s pour les zones d'allure)
- heart_rate : bpm (Garmin attend des bpm directs)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from garminconnect.workout import (
    ExecutableStep,
    RepeatGroup,
    RunningWorkout,
    WorkoutSegment,
    create_repeat_group,
)

from app.models import (
    DurationType as DBDurationType,
)
from app.models import (
    Session as DBSession,
)
from app.models import (
    Step as DBStep,
)
from app.models import (
    StepType as DBStepType,
)
from app.models import (
    TargetType as DBTargetType,
)


# --- Constantes Garmin ---


SPORT_RUNNING = {"sportTypeId": 1, "sportTypeKey": "running"}


_STEP_TYPE_MAP = {
    DBStepType.warmup: {"stepTypeId": 1, "stepTypeKey": "warmup", "displayOrder": 1},
    DBStepType.interval: {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
    DBStepType.recovery: {"stepTypeId": 4, "stepTypeKey": "recovery", "displayOrder": 4},
    DBStepType.cooldown: {"stepTypeId": 5, "stepTypeKey": "cooldown", "displayOrder": 5},
    DBStepType.other: {"stepTypeId": 7, "stepTypeKey": "other", "displayOrder": 7},
}

_END_CONDITION_TIME = {
    "conditionTypeId": 2,
    "conditionTypeKey": "time",
    "displayOrder": 2,
    "displayable": True,
}
_END_CONDITION_DISTANCE = {
    "conditionTypeId": 3,
    "conditionTypeKey": "distance",
    "displayOrder": 3,
    "displayable": True,
}
_END_CONDITION_LAP = {
    "conditionTypeId": 1,
    "conditionTypeKey": "lap.button",
    "displayOrder": 1,
    "displayable": True,
}

_TARGET_NO_TARGET = {
    "workoutTargetTypeId": 1,
    "workoutTargetTypeKey": "no.target",
    "displayOrder": 1,
}
_TARGET_PACE_ZONE = {
    "workoutTargetTypeId": 6,
    "workoutTargetTypeKey": "pace.zone",
    "displayOrder": 6,
}
_TARGET_HR_ZONE = {
    "workoutTargetTypeId": 4,
    "workoutTargetTypeKey": "heart.rate.zone",
    "displayOrder": 4,
}


# --- Helpers de conversion d'allure ---


def pace_min_per_km_to_m_per_s(minutes: float) -> float:
    """Convertit une allure en min/km vers une vitesse en m/s.

    Ex : 5:34/km = 5 + 34/60 = 5.567 min/km -> 1000/(5.567*60) = 2.994 m/s
    """
    if minutes <= 0:
        raise ValueError("Allure doit etre positive (min/km)")
    return 1000.0 / (minutes * 60.0)


def pace_str_to_min_per_km(pace: str) -> float:
    """Parse '5:34' ou '5:34/km' -> 5.5666... min/km."""
    p = pace.strip().lower().removesuffix("/km").strip()
    if ":" in p:
        m, s = p.split(":")
        return int(m) + int(s) / 60.0
    return float(p)


# --- Builder ---


@dataclass
class _StepRefs:
    next_order: int = 1
    # Facteur multiplicatif pour l'ajustement d'allure (+x% = plus lent)
    # Applique aux m/s : adjusted = m_per_s / (1 + adj/100)
    pace_factor: float = 1.0


def adjust_pace_m_per_s(value: float | None, adjustment_pct: float) -> float | None:
    """Applique un ajustement d'allure (en %) a une vitesse en m/s.

    +x% = plus lent (m/s diminue) ; -x% = plus rapide (m/s augmente).
    """
    if value is None:
        return None
    return value / (1.0 + adjustment_pct / 100.0)


def _end_condition_for(step: DBStep) -> tuple[dict[str, Any], float | None]:
    if step.duration_type == DBDurationType.time:
        # secondes
        return _END_CONDITION_TIME, float(step.duration_value or 0)
    if step.duration_type == DBDurationType.distance:
        # metres
        return _END_CONDITION_DISTANCE, float(step.duration_value or 0)
    return _END_CONDITION_LAP, None


def _target_payload(step: DBStep, pace_adjustment_pct: float = 0.0) -> dict[str, Any]:
    """Renvoie un dict {targetType, targetValueOne?, targetValueTwo?} pret a etre fusionne."""
    if step.target_type == DBTargetType.no_target or step.target_low is None:
        return {"targetType": _TARGET_NO_TARGET}

    if step.target_type == DBTargetType.pace:
        # En DB on stocke deja en m/s pour rester independant du format
        # Garmin attend low/high en m/s avec low <= high
        low = float(step.target_low)
        high = float(step.target_high if step.target_high is not None else step.target_low)
        # Ajustement d'allure (+pct = plus lent)
        if pace_adjustment_pct:
            low = adjust_pace_m_per_s(low, pace_adjustment_pct) or low
            high = adjust_pace_m_per_s(high, pace_adjustment_pct) or high
        if low > high:
            low, high = high, low
        return {
            "targetType": _TARGET_PACE_ZONE,
            "targetValueOne": low,
            "targetValueTwo": high,
        }

    if step.target_type == DBTargetType.heart_rate:
        # FC : on n'ajuste pas (le ressenti FC est plus stable que l'allure)
        low = float(step.target_low)
        high = float(step.target_high if step.target_high is not None else step.target_low)
        if low > high:
            low, high = high, low
        return {
            "targetType": _TARGET_HR_ZONE,
            "targetValueOne": low,
            "targetValueTwo": high,
        }

    return {"targetType": _TARGET_NO_TARGET}


def _build_executable(
    step: DBStep, order: int, pace_adjustment_pct: float = 0.0
) -> ExecutableStep:
    end_condition, end_value = _end_condition_for(step)
    target = _target_payload(step, pace_adjustment_pct)

    fields: dict[str, Any] = {
        "stepOrder": order,
        "stepType": _STEP_TYPE_MAP.get(step.type, _STEP_TYPE_MAP[DBStepType.interval]),
        "endCondition": end_condition,
        "endConditionValue": end_value,
        **target,
    }
    if step.notes or step.label:
        # Garmin ne stocke pas vraiment de label par step, on mappe en description
        fields["description"] = step.label or step.notes

    return ExecutableStep(**fields)


def _build_repeat(
    step: DBStep, children: list[DBStep], order: int, refs: _StepRefs
) -> RepeatGroup:
    iterations = step.repeat_count or 1
    workout_steps = []
    refs.next_order += 1  # le repeat occupe lui-meme un slot d'ordre
    for child in children:
        workout_steps.append(
            _build_executable(child, refs.next_order, refs.pace_factor)
        )
        refs.next_order += 1
    return create_repeat_group(
        iterations=iterations,
        workout_steps=workout_steps,
        step_order=order,
    )


def session_to_running_workout(
    session: DBSession, pace_adjustment_pct: float = 0.0
) -> RunningWorkout:
    """Transforme une seance DB en RunningWorkout Garmin.

    `pace_adjustment_pct` ajuste les targets d'allure (+x% = plus lent).
    Mettre 0 pour utiliser les valeurs telles quelles.
    """
    refs = _StepRefs(pace_factor=pace_adjustment_pct)

    # Trie les steps de top niveau (parent_repeat_id is null) par order
    top_level = sorted(
        [s for s in session.steps if s.parent_repeat_id is None],
        key=lambda s: s.order,
    )

    # Index des enfants par parent_repeat_id
    children_by_parent: dict[int, list[DBStep]] = {}
    for s in session.steps:
        if s.parent_repeat_id is not None:
            children_by_parent.setdefault(s.parent_repeat_id, []).append(s)
    for k in children_by_parent:
        children_by_parent[k].sort(key=lambda s: s.order)

    workout_steps: list[Any] = []
    for s in top_level:
        order = refs.next_order
        if s.kind == "repeat":
            workout_steps.append(
                _build_repeat(s, children_by_parent.get(s.id or 0, []), order, refs)
            )
        else:
            workout_steps.append(
                _build_executable(s, order, refs.pace_factor)
            )
            refs.next_order += 1

    segment = WorkoutSegment(
        segmentOrder=1,
        sportType=SPORT_RUNNING,
        workoutSteps=workout_steps,
    )

    # Si la session n'a pas d'estimation cachee (cas tests / nouvelle seance),
    # on calcule a la volee depuis les steps pour respecter la contrainte
    # 'estimatedDurationInSecs > 0' du modele Garmin.
    duration_min = session.estimated_duration_min
    if not duration_min:
        duration_min, _ = estimate_duration_and_distance(list(session.steps))
    estimated_secs = max(1, int((duration_min or 1) * 60))

    return RunningWorkout(
        workoutName=session.name[:100],
        sportType=SPORT_RUNNING,
        estimatedDurationInSecs=estimated_secs,
        workoutSegments=[segment],
        description=(session.description or "")[:1000] or None,
    )


# --- Helpers de pre-calcul (estimation duree/distance pour la liste) ---


def estimate_duration_and_distance(steps: list[DBStep]) -> tuple[int, float]:
    """Approxime duree (minutes) + distance (km) d'une seance.

    - Steps de type 'distance' contribuent directement a la distance.
    - Steps de type 'time' contribuent a la duree, et si l'allure est definie
      on en deduit la distance approximative.
    - Les RepeatGroups multiplient leurs enfants par repeat_count.
    """
    total_seconds = 0.0
    total_meters = 0.0

    # Index enfants
    children_by_parent: dict[int, list[DBStep]] = {}
    for s in steps:
        if s.parent_repeat_id is not None:
            children_by_parent.setdefault(s.parent_repeat_id, []).append(s)

    for s in sorted(steps, key=lambda x: x.order):
        if s.parent_repeat_id is not None:
            continue  # traite via le parent

        multiplier = s.repeat_count if s.kind == "repeat" else 1
        kids = children_by_parent.get(s.id or 0, []) if s.kind == "repeat" else [s]

        for kid in kids:
            secs, meters = _estimate_one(kid)
            total_seconds += secs * (multiplier or 1)
            total_meters += meters * (multiplier or 1)

    return round(total_seconds / 60), round(total_meters / 1000, 1)


def _estimate_one(step: DBStep) -> tuple[float, float]:
    if step.duration_type == DBDurationType.time:
        secs = float(step.duration_value or 0)
        meters = 0.0
        if (
            step.target_type == DBTargetType.pace
            and step.target_low
            and step.target_high
        ):
            avg_speed = (step.target_low + step.target_high) / 2.0  # m/s
            meters = avg_speed * secs
        elif step.target_type == DBTargetType.pace and step.target_low:
            meters = step.target_low * secs
        return secs, meters
    if step.duration_type == DBDurationType.distance:
        meters = float(step.duration_value or 0)
        secs = 0.0
        if (
            step.target_type == DBTargetType.pace
            and step.target_low
            and step.target_high
        ):
            avg_speed = (step.target_low + step.target_high) / 2.0
            if avg_speed > 0:
                secs = meters / avg_speed
        return secs, meters
    return 0.0, 0.0
