"""Import des seances Campus Coach (27 avril - 7 juin 2026).

Recrée à l'identique le programme Marathon de Valentin tel que vu sur
Campus Coach via Claude in Chrome.

Convention :
- 4 séances par semaine, distribuées Lun/Mer/Ven/Dim
- Saison clé = Mer (séance d'intensité), Dim = Sortie longue ou course
- Allures stockées en m/s (low/high : low = plus lent, high = plus rapide)

Usage :
    .venv/Scripts/python.exe -m scripts.import_campus_coach
"""

from __future__ import annotations

import datetime as _dt

from sqlmodel import Session as DBSession
from sqlmodel import select

from app.db import engine, init_db
from app.models import (
    DurationType,
    Session,
    Step,
    StepType,
    TargetType,
    User,
    UserRole,
)
from app.services.workout_builder import (
    estimate_duration_and_distance,
    pace_min_per_km_to_m_per_s,
    pace_str_to_min_per_km,
)


def pace(min_per_km_str: str) -> float:
    """'5:34' -> m/s."""
    return pace_min_per_km_to_m_per_s(pace_str_to_min_per_km(min_per_km_str))


def step(
    order: int,
    type: StepType,
    *,
    label: str | None = None,
    minutes: float | None = None,
    seconds: float | None = None,
    distance_km: float | None = None,
    pace_low: str | None = None,
    pace_high: str | None = None,
    parent: int | None = None,
) -> dict:
    """Construit un dict de step (sera transformé en row Step)."""
    s = {
        "order": order,
        "type": type,
        "label": label,
        "kind": "step",
        "parent_repeat_id_marker": parent,  # remplacé par l'id réel après insert
    }
    if minutes is not None:
        s["duration_type"] = DurationType.time
        s["duration_value"] = minutes * 60
    elif seconds is not None:
        s["duration_type"] = DurationType.time
        s["duration_value"] = seconds
    elif distance_km is not None:
        s["duration_type"] = DurationType.distance
        s["duration_value"] = distance_km * 1000
    else:
        s["duration_type"] = DurationType.lap_button
        s["duration_value"] = None

    if pace_low and pace_high:
        s["target_type"] = TargetType.pace
        s["target_low"] = pace(pace_high)  # plus rapide -> m/s plus grand : low en m/s = pace_high
        s["target_high"] = pace(pace_low)
        # Reorder so low <= high in m/s
        if s["target_low"] > s["target_high"]:
            s["target_low"], s["target_high"] = s["target_high"], s["target_low"]
    elif pace_low:
        s["target_type"] = TargetType.pace
        v = pace(pace_low)
        s["target_low"] = v
        s["target_high"] = v
    else:
        s["target_type"] = TargetType.no_target
        s["target_low"] = None
        s["target_high"] = None
    return s


def repeat(order: int, count: int) -> dict:
    return {
        "order": order,
        "kind": "repeat",
        "type": StepType.interval,  # inutilisé
        "label": None,
        "repeat_count": count,
        "duration_type": DurationType.time,
        "duration_value": None,
        "target_type": TargetType.no_target,
        "target_low": None,
        "target_high": None,
        "parent_repeat_id_marker": None,
    }


# --- Données extraites de Campus Coach ---


def _make_ef(date, name, num_label, minutes, dist_km, pace_low, pace_high, desc):
    return {
        "date": date,
        "name": name,
        "description": desc,
        "difficulty": 1,
        "num_label": num_label,
        "steps": [
            step(1, StepType.other, label="EF",
                 minutes=minutes, pace_low=pace_low, pace_high=pace_high)
        ],
    }


SESSIONS: list[dict] = [
    # === Week 27 avr - 03 mai ===
    _make_ef(_dt.date(2026, 4, 27), "Endurance Fondamentale", "1/4",
             40, 6.9, "5:34", "6:04",
             "Footing à courir 100% en endurance fondamentale."),
    {
        "date": _dt.date(2026, 4, 29),
        "name": "Séance intense facile",
        "description": "Petite séance avec du seuil pour garder les jambes actives sans trop pousser pendant la récupération. Enchaine 10 fois 1' au seuil 60 et 1' lent.",
        "difficulty": 2,
        "num_label": "2/4",
        "steps": [
            step(1, StepType.warmup, label="Échauffement EF",
                 minutes=15, pace_low="5:34", pace_high="6:04"),
            repeat(2, 10),
            step(3, StepType.interval, label="Seuil 60",
                 minutes=1, pace_low="4:20", parent=2),
            step(4, StepType.recovery, label="Lent",
                 minutes=1, parent=2),
            step(5, StepType.cooldown, label="Récupération EF",
                 minutes=10, pace_low="5:34", pace_high="6:04"),
        ],
    },
    _make_ef(_dt.date(2026, 5, 1), "Endurance Fondamentale", "3/4",
             30, 5.2, "5:34", "6:04",
             "Footing à courir 100% en endurance fondamentale."),
    _make_ef(_dt.date(2026, 5, 3), "Sortie Longue", "4/4",
             50, 8.6, "5:34", "6:04",
             "Sortie longue à courir 100% en endurance fondamentale."),

    # === Week 04 - 10 mai ===
    _make_ef(_dt.date(2026, 5, 4), "Endurance Fondamentale", "1/4",
             45, 7.9, "5:28", "5:58",
             "Footing à courir 100% en endurance fondamentale."),
    {
        "date": _dt.date(2026, 5, 6),
        "name": "Seuil 60",
        "description": "Séance de seuil 60' où tu vas enchaîner 7 répétitions de 3' à l'allure cible entrecoupées de 1'30 de footing lent pour récupérer.",
        "difficulty": 3,
        "num_label": "2/4",
        "steps": [
            step(1, StepType.warmup, label="Échauffement EF",
                 minutes=15, pace_low="5:28", pace_high="5:58"),
            repeat(2, 7),
            step(3, StepType.interval, label="Seuil 60",
                 minutes=3, pace_low="4:15", parent=2),
            step(4, StepType.recovery, label="Lent",
                 seconds=90, parent=2),
            step(5, StepType.cooldown, label="Récupération EF",
                 minutes=5, pace_low="5:28", pace_high="5:58"),
        ],
    },
    _make_ef(_dt.date(2026, 5, 8), "Endurance Fondamentale", "3/4",
             45, 7.9, "5:28", "5:58",
             "Footing à courir 100% en endurance fondamentale."),
    {
        "date": _dt.date(2026, 5, 10),
        "name": "Sortie Longue & Active",
        "description": "Sortie longue en endurance fondamentale avec un bloc actif FACULTATIF en deuxième moitié. Le bloc actif est composé de 3 séries de 12' composées de : 8' Tempo, puis 2' SweetSpot, suivies par 2' de récupération à allure lente.",
        "difficulty": 2,
        "num_label": "4/4",
        "steps": [
            step(1, StepType.warmup, label="Échauffement EF",
                 minutes=45, pace_low="5:28", pace_high="5:58"),
            repeat(2, 3),
            step(3, StepType.interval, label="Tempo",
                 minutes=8, pace_low="5:00", parent=2),
            step(4, StepType.interval, label="Sweet spot",
                 minutes=2, pace_low="4:29", parent=2),
            step(5, StepType.recovery, label="Lent",
                 minutes=2, parent=2),
            step(6, StepType.cooldown, label="Récupération EF",
                 minutes=9, pace_low="5:28", pace_high="5:58"),
        ],
    },

    # === Week 11 - 17 mai ===
    _make_ef(_dt.date(2026, 5, 11), "Endurance Fondamentale", "1/4",
             45, 7.9, "5:28", "5:58",
             "Footing à courir 100% en endurance fondamentale."),
    {
        "date": _dt.date(2026, 5, 13),
        "name": "Seuil 60",
        "description": "Séance de seuil 60' où tu vas enchaîner 6 répétitions de 4' à l'allure cible entrecoupées de 1'30 de footing lent pour récupérer.",
        "difficulty": 3,
        "num_label": "2/4",
        "steps": [
            step(1, StepType.warmup, label="Échauffement EF",
                 minutes=15, pace_low="5:28", pace_high="5:58"),
            repeat(2, 6),
            step(3, StepType.interval, label="Seuil 60",
                 minutes=4, pace_low="4:15", parent=2),
            step(4, StepType.recovery, label="Lent",
                 seconds=90, parent=2),
            step(5, StepType.cooldown, label="Récupération EF",
                 minutes=5, pace_low="5:28", pace_high="5:58"),
        ],
    },
    _make_ef(_dt.date(2026, 5, 15), "Endurance Fondamentale", "3/4",
             45, 7.9, "5:28", "5:58",
             "Footing à courir 100% en endurance fondamentale."),
    {
        "date": _dt.date(2026, 5, 17),
        "name": "Sortie Longue & Active",
        "description": "Sortie longue en endurance fondamentale avec un bloc actif FACULTATIF en deuxième moitié. Le bloc actif est composé de 30' Tempo en continu.",
        "difficulty": 2,
        "num_label": "4/4",
        "steps": [
            step(1, StepType.warmup, label="Échauffement EF",
                 minutes=60, pace_low="5:28", pace_high="5:58"),
            step(2, StepType.interval, label="Tempo",
                 minutes=30, pace_low="5:00"),
            step(3, StepType.cooldown, label="Récupération EF",
                 minutes=10, pace_low="5:28", pace_high="5:58"),
        ],
    },

    # === Week 18 - 24 mai (semaine de récupération) ===
    _make_ef(_dt.date(2026, 5, 18), "Endurance Fondamentale", "1/4",
             30, 5.3, "5:27", "5:57",
             "Footing à courir 100% en endurance fondamentale (semaine de récup)."),
    {
        "date": _dt.date(2026, 5, 20),
        "name": "Séance intense facile",
        "description": "Petite séance avec du seuil. Enchaine 5 fois 2' au Seuil 60 et 2' lent pour récupérer.",
        "difficulty": 2,
        "num_label": "2/4",
        "steps": [
            step(1, StepType.warmup, label="Échauffement EF",
                 minutes=15, pace_low="5:27", pace_high="5:57"),
            repeat(2, 5),
            step(3, StepType.interval, label="Seuil 60",
                 minutes=2, pace_low="4:15", parent=2),
            step(4, StepType.recovery, label="Lent",
                 minutes=2, parent=2),
            step(5, StepType.cooldown, label="Récupération EF",
                 minutes=10, pace_low="5:27", pace_high="5:57"),
        ],
    },
    _make_ef(_dt.date(2026, 5, 22), "Endurance Fondamentale", "3/4",
             30, 5.3, "5:27", "5:57",
             "Footing à courir 100% en endurance fondamentale (semaine de récup)."),
    _make_ef(_dt.date(2026, 5, 24), "Sortie Longue", "4/4",
             70, 12.3, "5:27", "5:57",
             "Sortie longue à courir 100% en endurance fondamentale."),

    # === Week 25 - 31 mai ===
    _make_ef(_dt.date(2026, 5, 25), "Endurance Fondamentale", "1/4",
             45, 7.9, "5:26", "5:56",
             "Footing à courir 100% en endurance fondamentale."),
    {
        "date": _dt.date(2026, 5, 27),
        "name": "Endurance de Force",
        "description": "Séance d'endurance de force en deux blocs. 3 intervalles par bloc (35\", 25\", 15\") séparés par 3' de repos lent. À la fin du premier bloc prends 5' (2' de bonus) puis seconde fois.",
        "difficulty": 3,
        "num_label": "2/4",
        "steps": [
            step(1, StepType.warmup, label="Échauffement EF",
                 minutes=20, pace_low="5:26", pace_high="5:56"),
            repeat(2, 2),
            step(3, StepType.interval, label="Rapide", seconds=35, parent=2),
            step(4, StepType.recovery, label="Lent", minutes=3, parent=2),
            step(5, StepType.interval, label="Rapide", seconds=25, parent=2),
            step(6, StepType.recovery, label="Lent", minutes=3, parent=2),
            step(7, StepType.interval, label="Rapide", seconds=15, parent=2),
            step(8, StepType.recovery, label="Récup EF", minutes=5,
                 pace_low="5:26", pace_high="5:56", parent=2),
            step(9, StepType.cooldown, label="Récupération EF",
                 minutes=10, pace_low="5:26", pace_high="5:56"),
        ],
    },
    _make_ef(_dt.date(2026, 5, 29), "Endurance Fondamentale", "3/4",
             45, 7.9, "5:26", "5:56",
             "Footing à courir 100% en endurance fondamentale."),
    {
        "date": _dt.date(2026, 5, 31),
        "name": "Sortie Longue & Active",
        "description": "Sortie longue en endurance fondamentale avec un bloc actif FACULTATIF. Le bloc actif est composé de 25' Tempo en continu.",
        "difficulty": 2,
        "num_label": "4/4",
        "steps": [
            step(1, StepType.warmup, label="Échauffement EF",
                 minutes=45, pace_low="5:26", pace_high="5:56"),
            step(2, StepType.interval, label="Tempo",
                 minutes=25, pace_low="4:58"),
            step(3, StepType.cooldown, label="Récupération EF",
                 minutes=10, pace_low="5:26", pace_high="5:56"),
        ],
    },

    # === Week 01 - 07 juin (course 10km dimanche) ===
    _make_ef(_dt.date(2026, 6, 1), "Endurance Fondamentale", "1/4",
             40, 7.1, "5:25", "5:55",
             "Footing à courir 100% en endurance fondamentale."),
    {
        "date": _dt.date(2026, 6, 3),
        "name": "Endurance de Force",
        "description": "Séance d'endurance de force en deux blocs identiques. 3 intervalles de 30\" par bloc, chacun suivi par 3' de repos lent. 5' entre les blocs.",
        "difficulty": 3,
        "num_label": "2/4",
        "steps": [
            step(1, StepType.warmup, label="Échauffement EF",
                 minutes=20, pace_low="5:25", pace_high="5:55"),
            # Bloc 1
            repeat(2, 3),
            step(3, StepType.interval, label="Rapide", seconds=30, parent=2),
            step(4, StepType.recovery, label="Lent", minutes=3, parent=2),
            # Transition
            step(5, StepType.recovery, label="Lent (transition)",
                 minutes=2),
            # Bloc 2
            repeat(6, 3),
            step(7, StepType.interval, label="Rapide", seconds=30, parent=6),
            step(8, StepType.recovery, label="Lent", minutes=3, parent=6),
            step(9, StepType.cooldown, label="Récupération EF",
                 minutes=10, pace_low="5:25", pace_high="5:55"),
        ],
    },
    _make_ef(_dt.date(2026, 6, 5), "Endurance Fondamentale", "3/4",
             40, 7.1, "5:25", "5:55",
             "Footing à courir 100% en endurance fondamentale."),
    {
        "date": _dt.date(2026, 6, 7),
        "name": "Course 10km",
        "description": "Course intermédiaire (10 km).",
        "difficulty": 3,
        "num_label": "4/4",
        "steps": [
            step(1, StepType.other, label="Course 10km",
                 distance_km=10),
        ],
    },
]


def main() -> None:
    init_db()

    with DBSession(engine) as db:
        athlete = db.exec(
            select(User).where(User.role == UserRole.athlete)
        ).first()
        if athlete is None:
            raise SystemExit("Aucun athlete configure (lance le serveur une fois pour seeder)")

        # Wipe les seances existantes pour cet athlete
        existing = db.exec(
            select(Session).where(Session.athlete_id == athlete.id)
        ).all()
        for s in existing:
            db.delete(s)
        db.flush()
        print(f"[wipe] {len(existing)} anciennes seances supprimees")

        for sdata in SESSIONS:
            session = Session(
                athlete_id=athlete.id,
                date=sdata["date"],
                name=sdata["name"],
                description=sdata.get("description"),
                difficulty=sdata.get("difficulty", 1),
            )
            db.add(session)
            db.flush()  # garantit session.id

            # Insert les steps en deux passes (repeat d'abord, enfants ensuite)
            id_by_marker: dict[int, int] = {}

            # Pass 1 : steps top-level (sans parent) et repeat
            for sd in sdata["steps"]:
                if sd.get("parent_repeat_id_marker") is None:
                    new_step = Step(
                        session_id=session.id,
                        order=sd["order"],
                        kind=sd["kind"],
                        label=sd.get("label"),
                        type=sd["type"],
                        duration_type=sd["duration_type"],
                        duration_value=sd["duration_value"],
                        target_type=sd["target_type"],
                        target_low=sd.get("target_low"),
                        target_high=sd.get("target_high"),
                        repeat_count=sd.get("repeat_count"),
                    )
                    db.add(new_step)
                    db.flush()
                    if sd["kind"] == "repeat":
                        id_by_marker[sd["order"]] = new_step.id

            # Pass 2 : steps enfants
            for sd in sdata["steps"]:
                marker = sd.get("parent_repeat_id_marker")
                if marker is not None:
                    parent_id = id_by_marker.get(marker)
                    new_step = Step(
                        session_id=session.id,
                        order=sd["order"],
                        kind=sd["kind"],
                        label=sd.get("label"),
                        type=sd["type"],
                        duration_type=sd["duration_type"],
                        duration_value=sd["duration_value"],
                        target_type=sd["target_type"],
                        target_low=sd.get("target_low"),
                        target_high=sd.get("target_high"),
                        parent_repeat_id=parent_id,
                    )
                    db.add(new_step)
                    db.flush()

            # Recharge les steps + recompute estimation
            db.refresh(session)
            duration_min, distance_km = estimate_duration_and_distance(list(session.steps))
            session.estimated_duration_min = duration_min
            session.estimated_distance_km = distance_km
            db.add(session)

            print(
                f"[ok] {sdata['date']} {sdata['name']!r:35s} "
                f"-> {duration_min} min · {distance_km} km · "
                f"{len(sdata['steps'])} steps"
            )

        db.commit()
        print(f"\n{len(SESSIONS)} seances importees pour {athlete.name} ({athlete.email})")


if __name__ == "__main__":
    main()
