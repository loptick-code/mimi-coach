"""Tests du builder Session -> RunningWorkout.

On valide :
- Conversion d'allure min/km <-> m/s
- Construction d'une seance simple (1 step)
- Construction d'une seance complexe avec bloc repete (cas Campus Coach
  "Seance intense facile" : EF 15min + 10x(1' seuil + 1' lent) + RC)
- Calcul des estimations duree/distance
"""

from __future__ import annotations

import math

import pytest

from app.models import (
    DurationType,
    Session,
    Step,
    StepType,
    TargetType,
)
from app.services.workout_builder import (
    estimate_duration_and_distance,
    pace_min_per_km_to_m_per_s,
    pace_str_to_min_per_km,
    session_to_running_workout,
)


# --- Helpers ---


def _make_step(
    order: int,
    *,
    type=StepType.interval,
    label=None,
    duration_type=DurationType.time,
    duration_value=None,
    target_type=TargetType.no_target,
    target_low=None,
    target_high=None,
    kind="step",
    repeat_count=None,
    parent_repeat_id=None,
) -> Step:
    return Step(
        id=order,
        session_id=1,
        order=order,
        type=type,
        label=label,
        duration_type=duration_type,
        duration_value=duration_value,
        target_type=target_type,
        target_low=target_low,
        target_high=target_high,
        kind=kind,
        repeat_count=repeat_count,
        parent_repeat_id=parent_repeat_id,
    )


def _make_session(steps: list[Step], name: str = "Test") -> Session:
    s = Session(
        id=1,
        athlete_id=1,
        date=__import__("datetime").date(2026, 4, 27),
        name=name,
    )
    s.steps = steps
    return s


# --- Pace conversion ---


def test_pace_str_parse():
    assert pace_str_to_min_per_km("5:34") == pytest.approx(5 + 34 / 60)
    assert pace_str_to_min_per_km("5:34/km") == pytest.approx(5 + 34 / 60)
    assert pace_str_to_min_per_km("4:00") == pytest.approx(4.0)


def test_pace_to_m_per_s():
    # 5:00/km = 1000m / 300s = 3.333 m/s
    assert pace_min_per_km_to_m_per_s(5.0) == pytest.approx(3.333, rel=1e-3)
    # 4:20/km = 1000/(4*60+20) = 1000/260 = 3.846 m/s
    assert pace_min_per_km_to_m_per_s(4 + 20 / 60) == pytest.approx(3.846, rel=1e-3)


def test_pace_invalid():
    with pytest.raises(ValueError):
        pace_min_per_km_to_m_per_s(0)


# --- Simple session ---


def test_simple_continuous_run():
    """30 min EF a 5:34-6:04/km."""
    low_speed = pace_min_per_km_to_m_per_s(6 + 4 / 60)  # 6:04 -> ~2.747 m/s
    high_speed = pace_min_per_km_to_m_per_s(5 + 34 / 60)  # 5:34 -> ~2.994 m/s

    steps = [
        _make_step(
            1,
            type=StepType.other,
            label="EF",
            duration_value=30 * 60,
            target_type=TargetType.pace,
            target_low=low_speed,
            target_high=high_speed,
        )
    ]
    session = _make_session(steps, "Endurance Fondamentale")
    rw = session_to_running_workout(session)
    payload = rw.to_dict()

    segments = payload["workoutSegments"]
    assert len(segments) == 1
    workout_steps = segments[0]["workoutSteps"]
    assert len(workout_steps) == 1

    step = workout_steps[0]
    assert step["stepType"]["stepTypeKey"] == "other"
    assert step["endCondition"]["conditionTypeKey"] == "time"
    assert step["endConditionValue"] == 30 * 60
    assert step["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert math.isclose(step["targetValueOne"], low_speed, rel_tol=1e-3)
    assert math.isclose(step["targetValueTwo"], high_speed, rel_tol=1e-3)


# --- Repeat group (cas Campus Coach) ---


def test_repeat_group_seance_intense_facile():
    """Reproduit la 'Seance intense facile' vue sur Campus Coach :

    - Echauffement EF 15 min @ 5:34-6:04/km
    - 10 x (1 min Seuil 60 @ 4:20/km + 1 min recup lent)
    - Retour calme 10 min
    """
    ef_low = pace_min_per_km_to_m_per_s(6 + 4 / 60)
    ef_high = pace_min_per_km_to_m_per_s(5 + 34 / 60)
    seuil = pace_min_per_km_to_m_per_s(4 + 20 / 60)

    steps = [
        # 1) Echauffement
        _make_step(
            1,
            type=StepType.warmup,
            label="EF",
            duration_value=15 * 60,
            target_type=TargetType.pace,
            target_low=ef_low,
            target_high=ef_high,
        ),
        # 2) Repeat marker
        _make_step(2, kind="repeat", repeat_count=10),
        # 3) Seuil 60 (enfant du repeat)
        _make_step(
            3,
            type=StepType.interval,
            label="Seuil 60",
            duration_value=60,
            target_type=TargetType.pace,
            target_low=seuil,
            target_high=seuil,
            parent_repeat_id=2,
        ),
        # 4) Recup (enfant du repeat)
        _make_step(
            4,
            type=StepType.recovery,
            label="lent",
            duration_value=60,
            target_type=TargetType.no_target,
            parent_repeat_id=2,
        ),
        # 5) Retour calme
        _make_step(
            5,
            type=StepType.cooldown,
            label="RC",
            duration_value=10 * 60,
            target_type=TargetType.no_target,
        ),
    ]
    session = _make_session(steps, "Seance intense facile")
    rw = session_to_running_workout(session)
    payload = rw.to_dict()

    workout_steps = payload["workoutSegments"][0]["workoutSteps"]
    # 3 elements top-level : warmup, repeat, cooldown
    assert len(workout_steps) == 3

    warmup, repeat, cooldown = workout_steps
    assert warmup["stepType"]["stepTypeKey"] == "warmup"
    assert cooldown["stepType"]["stepTypeKey"] == "cooldown"

    assert repeat["type"] == "RepeatGroupDTO"
    assert repeat["stepType"]["stepTypeKey"] == "repeat"
    assert repeat["numberOfIterations"] == 10
    assert len(repeat["workoutSteps"]) == 2
    seuil_step, recup_step = repeat["workoutSteps"]
    assert seuil_step["stepType"]["stepTypeKey"] == "interval"
    assert seuil_step["endConditionValue"] == 60.0
    assert math.isclose(seuil_step["targetValueOne"], seuil, rel_tol=1e-3)
    assert recup_step["stepType"]["stepTypeKey"] == "recovery"
    assert recup_step["targetType"]["workoutTargetTypeKey"] == "no.target"


# --- Estimations ---


def test_estimate_duration_simple():
    steps = [
        _make_step(1, duration_value=30 * 60),
    ]
    duration, distance = estimate_duration_and_distance(steps)
    assert duration == 30
    assert distance == 0.0  # pas d'allure -> pas de distance


def test_estimate_duration_with_pace():
    """30 min @ 5:00/km = 6 km."""
    speed = pace_min_per_km_to_m_per_s(5.0)
    steps = [
        _make_step(
            1,
            duration_value=30 * 60,
            target_type=TargetType.pace,
            target_low=speed,
            target_high=speed,
        ),
    ]
    duration, distance = estimate_duration_and_distance(steps)
    assert duration == 30
    assert distance == 6.0


def test_estimate_with_repeat():
    """15 min warmup + 10x(1' + 1') + 10 min cooldown = 45 min."""
    steps = [
        _make_step(1, type=StepType.warmup, duration_value=15 * 60),
        _make_step(2, kind="repeat", repeat_count=10),
        _make_step(3, duration_value=60, parent_repeat_id=2),
        _make_step(4, duration_value=60, parent_repeat_id=2),
        _make_step(5, type=StepType.cooldown, duration_value=10 * 60),
    ]
    duration, distance = estimate_duration_and_distance(steps)
    assert duration == 45
    # pas d'allure -> distance = 0
    assert distance == 0.0


def test_estimate_distance_step():
    """5 km en distance + 1 km en distance = 6 km."""
    steps = [
        _make_step(1, duration_type=DurationType.distance, duration_value=5000),
        _make_step(2, duration_type=DurationType.distance, duration_value=1000),
    ]
    _, distance = estimate_duration_and_distance(steps)
    assert distance == 6.0


# --- Edge cases ---


def test_pace_low_high_swapped_is_normalized():
    """Si on entre target_low > target_high, on attend une normalisation pour Garmin."""
    # En m/s, low doit etre plus petit que high. On simule l'inverse.
    steps = [
        _make_step(
            1,
            type=StepType.interval,
            duration_value=60,
            target_type=TargetType.pace,
            target_low=4.0,  # plus rapide
            target_high=3.0,  # plus lent
        )
    ]
    rw = session_to_running_workout(_make_session(steps))
    step = rw.to_dict()["workoutSegments"][0]["workoutSteps"][0]
    assert step["targetValueOne"] <= step["targetValueTwo"]


def test_lap_button_no_end_condition_value():
    steps = [
        _make_step(1, duration_type=DurationType.lap_button),
    ]
    rw = session_to_running_workout(_make_session(steps))
    step = rw.to_dict()["workoutSegments"][0]["workoutSteps"][0]
    assert step["endCondition"]["conditionTypeKey"] == "lap.button"
    assert step.get("endConditionValue") is None
