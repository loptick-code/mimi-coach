"""Importe le plan marathon 16 semaines (Nice-Cannes, 08/11/2026) pour Malorie et Aureline.

Source : scratchpad/plan_marathon_8_nov_2026.md (dates + allures deja decalees
de +30 sec/km, semaine par semaine, allures NON constantes - elles suivent la
vraie progression observee dans scratchpad/extract_seances_campus_coach.md,
un extrait detaille du plan reel de Valentin sur Campus Coach, semaine par
semaine cette fois - pas juste quelques exemples).

Toutes les allures ci-dessous sont deja les allures cibles finales (deja
decalees), on ne reapplique aucun shift ici.

Fidelite aux donnees sources :
- Quand un bloc repete n'affichait pas explicitement son nombre de repetitions
  (ex : seances "intense facile"), le nombre est deduit de la duree totale
  donnee - conformement a la methodologie documentee dans l'extrait source.
- Quand un bloc est decrit comme une sequence fixe non repetee (pyramides
  Khéops, blocs "Force + Allure 42km", "Long + Allure 42 km", SweetSpot,
  Allure 42), la structure est transcrite LITTERALEMENT telle que decrite,
  sans ajouter de contenu invente pour forcer la correspondance avec le total
  "Apercu hebdo" affiche par Campus Coach. Sur plusieurs semaines (2, 9-11,
  13, 15, 16), la decomposition detaillee ne totalise pas exactement la duree
  d'apercu affichee par Campus Coach - cet ecart existe deja dans l'extrait
  brut (donc probablement du a un arrondi/buffer GPS non capture par le
  texte), il n'est pas cree par cet import. Duree/distance affichees dans
  l'app = calculees a partir de la decomposition litterale, pas de l'apercu.
- Semaine 10 : donnees source elles-memes signalees comme interpolees par
  l'auteur de l'extrait (activite Campus Coach sans distance affichee).

Usage :
    .venv/Scripts/python.exe -m scripts.import_marathon_16w            # exécute
    .venv/Scripts/python.exe -m scripts.import_marathon_16w --dry-run  # apercu sans ecrire
"""

from __future__ import annotations

import argparse
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

MONDAY_W1 = _dt.date(2026, 7, 20)  # semaine 1 ; marathon = dimanche 8 nov 2026 (semaine 16)


def pace(min_per_km_str: str) -> float:
    """'6:04' -> m/s. Les allures dans ce fichier sont deja les allures finales (deja decalees)."""
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
    s = {
        "order": order,
        "type": type,
        "label": label,
        "kind": "step",
        "parent_repeat_id_marker": parent,
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
        s["target_low"] = pace(pace_high)
        s["target_high"] = pace(pace_low)
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
        "type": StepType.interval,
        "label": None,
        "repeat_count": count,
        "duration_type": DurationType.time,
        "duration_value": None,
        "target_type": TargetType.no_target,
        "target_low": None,
        "target_high": None,
        "parent_repeat_id_marker": None,
    }


def ef(date, num_label, minutes, dist_km, pace_low, pace_high, difficulty=1, name="Endurance Fondamentale", note=""):
    return {
        "date": date,
        "name": name,
        "description": f"Continu a {pace_low}-{pace_high}/km." + note,
        "difficulty": difficulty,
        "num_label": num_label,
        "steps": [step(1, StepType.other, label="EF", minutes=minutes, pace_low=pace_low, pace_high=pace_high)],
    }


def mon(week_idx: int) -> _dt.date:
    return MONDAY_W1 + _dt.timedelta(weeks=week_idx - 1)


def d(week_idx: int, offset: int) -> _dt.date:
    return mon(week_idx) + _dt.timedelta(days=offset)


# --- Programme : 16 semaines, dates et allures deja finales ---

WEEKS: list[list[dict]] = [
    # ===== Semaine 1 (20-26 juil) =====
    [
        ef(d(1, 0), "1/4", 40, 6.6, "6:04", "6:34"),
        {
            "date": d(1, 2), "name": "Endurance de Force : « Lame de Fond »", "difficulty": 4, "num_label": "2/4",
            "description": "20 min a 6:20-6:50/km + 6 repetitions de 30s rapide (effort libre) / 3 min recup lente + 10 min recup a 6:20-6:50/km.",
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=20, pace_low="6:20", pace_high="6:50"),
                repeat(2, 6),
                step(3, StepType.interval, label="Rapide", seconds=30, parent=2),
                step(4, StepType.recovery, label="Lent", minutes=3, parent=2),
                step(5, StepType.cooldown, label="Recuperation", minutes=10, pace_low="6:20", pace_high="6:50"),
            ],
        },
        ef(d(1, 4), "3/4", 40, 6.6, "6:04", "6:34"),
        {
            "date": d(1, 6), "name": "Sortie Longue & Active", "difficulty": 3, "num_label": "4/4",
            "description": "45 min a 6:20-6:50/km + 25 min Tempo a 5:50/km + 10 min recup a 6:20-6:50/km.",
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=45, pace_low="6:20", pace_high="6:50"),
                step(2, StepType.interval, label="Tempo", minutes=25, pace_low="5:50"),
                step(3, StepType.cooldown, label="Recuperation", minutes=10, pace_low="6:20", pace_high="6:50"),
            ],
        },
    ],
    # ===== Semaine 2 (27 juil - 2 aout) =====
    [
        ef(d(2, 0), "1/4", 40, 6.6, "6:04", "6:34"),
        {
            "date": d(2, 2), "name": "Endurance de Force : « Khéops »", "difficulty": 4, "num_label": "2/4",
            "description": (
                "20 min a 6:19-6:49/km + pyramide d'efforts (10s / 20s / 30s / 30s / 20s / 10s, effort libre) "
                "avec 3 min recup lente entre chaque palier + 10 min recup a 6:19-6:49/km. "
                "Note : la decomposition ci-dessus totalise 47 min ; Campus Coach affichait 1h17 pour cette "
                "seance - ecart present dans la source, non reconstitue ici (pas de contenu invente)."
            ),
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=20, pace_low="6:19", pace_high="6:49"),
                step(2, StepType.interval, label="Sprint", seconds=10),
                step(3, StepType.recovery, label="Lent", minutes=3),
                step(4, StepType.interval, label="Rapide", seconds=20),
                step(5, StepType.recovery, label="Lent", minutes=3),
                step(6, StepType.interval, label="Rapide", seconds=30),
                step(7, StepType.recovery, label="Lent", minutes=3),
                step(8, StepType.interval, label="Rapide", seconds=30),
                step(9, StepType.recovery, label="Lent", minutes=3),
                step(10, StepType.interval, label="Rapide", seconds=20),
                step(11, StepType.recovery, label="Lent", minutes=3),
                step(12, StepType.interval, label="Sprint", seconds=10),
                step(13, StepType.cooldown, label="Recuperation", minutes=10, pace_low="6:19", pace_high="6:49"),
            ],
        },
        ef(d(2, 4), "3/4", 40, 6.6, "6:04", "6:34"),
        {
            "date": d(2, 6), "name": "Sortie Longue & Active", "difficulty": 3, "num_label": "4/4",
            "description": "Structure Tempo similaire a la semaine 1 : 45 min a 6:20-6:50/km + 25 min Tempo a 5:50/km + 10 min recup a 6:20-6:50/km.",
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=45, pace_low="6:20", pace_high="6:50"),
                step(2, StepType.interval, label="Tempo", minutes=25, pace_low="5:50"),
                step(3, StepType.cooldown, label="Recuperation", minutes=10, pace_low="6:20", pace_high="6:50"),
            ],
        },
    ],
    # ===== Semaine 3 (3-9 aout) =====
    [
        ef(d(3, 0), "1/4", 40, 6.6, "6:04", "6:34"),
        {
            "date": d(3, 2), "name": "Endurance de Force : « Avis de Tempête »", "difficulty": 4, "num_label": "2/4",
            "description": "20 min a 6:18-6:48/km + 7 repetitions de 30s rapide (effort libre) / 3 min recup lente + 10 min recup a 6:18-6:48/km.",
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=20, pace_low="6:18", pace_high="6:48"),
                repeat(2, 7),
                step(3, StepType.interval, label="Rapide", seconds=30, parent=2),
                step(4, StepType.recovery, label="Lent", minutes=3, parent=2),
                step(5, StepType.cooldown, label="Recuperation", minutes=10, pace_low="6:18", pace_high="6:48"),
            ],
        },
        ef(d(3, 4), "3/4", 40, 6.6, "6:04", "6:34"),
        {
            "date": d(3, 6), "name": "Sortie Longue & Active", "difficulty": 3, "num_label": "4/4",
            "description": "Structure Tempo (non revalidee individuellement dans la source) : 55 min a 6:18-6:48/km + 25 min Tempo a 5:50/km + 10 min recup a 6:18-6:48/km.",
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=55, pace_low="6:18", pace_high="6:48"),
                step(2, StepType.interval, label="Tempo", minutes=25, pace_low="5:50"),
                step(3, StepType.cooldown, label="Recuperation", minutes=10, pace_low="6:18", pace_high="6:48"),
            ],
        },
    ],
    # ===== Semaine 4 (10-16 aout, RECUPERATION) =====
    [
        ef(d(4, 0), "1/4", 30, 5.0, "6:04", "6:34", note=" (semaine de recuperation, allegee)"),
        {
            "date": d(4, 2), "name": "Séance intense facile", "difficulty": 2, "num_label": "2/4",
            "description": (
                "15 min a 6:17-6:47/km + 9 repetitions de (1 min Tempo a 5:48/km + 1 min Seuil 60 a 5:00/km + "
                "15s VMA a 4:14/km) + 2 min recup lente + 10 min recup a 6:17-6:47/km. Nombre de repetitions "
                "deduit de la duree totale (47 min) - non explicitement affiche par Campus Coach."
            ),
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=15, pace_low="6:17", pace_high="6:47"),
                repeat(2, 9),
                step(3, StepType.interval, label="Tempo", minutes=1, pace_low="5:48", parent=2),
                step(4, StepType.interval, label="Seuil 60", minutes=1, pace_low="5:00", parent=2),
                step(5, StepType.interval, label="VMA", seconds=15, pace_low="4:14", parent=2),
                step(6, StepType.recovery, label="Lent", minutes=2),
                step(7, StepType.cooldown, label="Recuperation", minutes=10, pace_low="6:17", pace_high="6:47"),
            ],
        },
        ef(d(4, 4), "3/4", 30, 5.0, "6:04", "6:34", note=" (semaine de recuperation, allegee)"),
        ef(d(4, 6), "4/4", 60, 9.9, "6:17", "6:47", difficulty=2, name="Sortie Longue",
           note=" (sans bloc rapide, semaine de recuperation)"),
    ],
    # ===== Semaine 5 (17-23 aout) =====
    [
        ef(d(5, 0), "1/4", 40, 6.7, "6:04", "6:34"),
        {
            "date": d(5, 2), "name": "Seuil 60", "difficulty": 3, "num_label": "2/4",
            "description": "15 min a 6:15-6:45/km + 6 repetitions de 3 min a 4:58/km / 1:30 recup lente + 5 min recup a 6:15-6:45/km.",
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=15, pace_low="6:15", pace_high="6:45"),
                repeat(2, 6),
                step(3, StepType.interval, label="Seuil 60", minutes=3, pace_low="4:58", parent=2),
                step(4, StepType.recovery, label="Lent", seconds=90, parent=2),
                step(5, StepType.cooldown, label="Recuperation", minutes=5, pace_low="6:15", pace_high="6:45"),
            ],
        },
        ef(d(5, 4), "3/4", 40, 6.7, "6:04", "6:34"),
        {
            "date": d(5, 6), "name": "Sortie Longue & Active", "difficulty": 3, "num_label": "4/4",
            "description": "Structure Tempo (non revalidee individuellement) : 55 min a 6:15-6:45/km + 25 min Tempo a 5:50/km + 10 min recup a 6:15-6:45/km.",
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=55, pace_low="6:15", pace_high="6:45"),
                step(2, StepType.interval, label="Tempo", minutes=25, pace_low="5:50"),
                step(3, StepType.cooldown, label="Recuperation", minutes=10, pace_low="6:15", pace_high="6:45"),
            ],
        },
    ],
    # ===== Semaine 6 (24-30 aout) =====
    [
        ef(d(6, 0), "1/4", 40, 6.7, "6:04", "6:34"),
        {
            "date": d(6, 2), "name": "Seuil 60", "difficulty": 3, "num_label": "2/4",
            "description": "15 min a 6:14-6:44/km + 8 repetitions de 4 min a 4:57/km / 2 min recup lente + 5 min recup a 6:14-6:44/km.",
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=15, pace_low="6:14", pace_high="6:44"),
                repeat(2, 8),
                step(3, StepType.interval, label="Seuil 60", minutes=4, pace_low="4:57", parent=2),
                step(4, StepType.recovery, label="Lent", minutes=2, parent=2),
                step(5, StepType.cooldown, label="Recuperation", minutes=5, pace_low="6:14", pace_high="6:44"),
            ],
        },
        ef(d(6, 4), "3/4", 40, 6.7, "6:04", "6:34"),
        {
            "date": d(6, 6), "name": "Sortie Longue & Active", "difficulty": 3, "num_label": "4/4",
            "description": "Structure Tempo (non revalidee individuellement) : 65 min a 6:14-6:44/km + 25 min Tempo a 5:50/km + 10 min recup a 6:14-6:44/km.",
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=65, pace_low="6:14", pace_high="6:44"),
                step(2, StepType.interval, label="Tempo", minutes=25, pace_low="5:50"),
                step(3, StepType.cooldown, label="Recuperation", minutes=10, pace_low="6:14", pace_high="6:44"),
            ],
        },
    ],
    # ===== Semaine 7 (31 aout - 6 sept) =====
    [
        ef(d(7, 0), "1/4", 40, 6.7, "6:04", "6:34"),
        {
            "date": d(7, 2), "name": "Séance S60 alternative : « Big Five »", "difficulty": 3, "num_label": "2/4",
            "description": "15 min a 6:12-6:42/km + 5 repetitions de (2 min Seuil 30 a 4:43/km + 3 min SweetSpot a 5:10/km + 2 min recup lente) + 5 min recup a 6:12-6:42/km.",
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=15, pace_low="6:12", pace_high="6:42"),
                repeat(2, 5),
                step(3, StepType.interval, label="Seuil 30", minutes=2, pace_low="4:43", parent=2),
                step(4, StepType.interval, label="SweetSpot", minutes=3, pace_low="5:10", parent=2),
                step(5, StepType.recovery, label="Lent", minutes=2, parent=2),
                step(6, StepType.cooldown, label="Recuperation", minutes=5, pace_low="6:12", pace_high="6:42"),
            ],
        },
        ef(d(7, 4), "3/4", 40, 6.7, "6:04", "6:34"),
        {
            "date": d(7, 6), "name": "Sortie Longue & Active", "difficulty": 3, "num_label": "4/4",
            "description": "Structure Tempo (non revalidee individuellement) : 75 min a 6:12-6:42/km + 25 min Tempo a 5:50/km + 10 min recup a 6:12-6:42/km.",
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=75, pace_low="6:12", pace_high="6:42"),
                step(2, StepType.interval, label="Tempo", minutes=25, pace_low="5:50"),
                step(3, StepType.cooldown, label="Recuperation", minutes=10, pace_low="6:12", pace_high="6:42"),
            ],
        },
    ],
    # ===== Semaine 8 (7-13 sept, RECUPERATION) =====
    [
        ef(d(8, 0), "1/4", 25, 4.2, "6:04", "6:34", note=" (semaine de recuperation, allegee)"),
        {
            "date": d(8, 2), "name": "Séance intense facile", "difficulty": 2, "num_label": "2/4",
            "description": (
                "15 min a 6:11-6:41/km + 8 repetitions de 1 min Seuil 60 a 4:55/km / 1 min recup lente "
                "+ 10 min recup a 6:11-6:41/km. Nombre de repetitions deduit de la duree totale (~41 min)."
            ),
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=15, pace_low="6:11", pace_high="6:41"),
                repeat(2, 8),
                step(3, StepType.interval, label="Seuil 60", minutes=1, pace_low="4:55", parent=2),
                step(4, StepType.recovery, label="Lent", minutes=1, parent=2),
                step(5, StepType.cooldown, label="Recuperation", minutes=10, pace_low="6:11", pace_high="6:41"),
            ],
        },
        ef(d(8, 4), "3/4", 30, 5.1, "6:04", "6:34", note=" (semaine de recuperation, allegee)"),
        ef(d(8, 6), "4/4", 75, 12.6, "6:11", "6:41", difficulty=2, name="Sortie Longue",
           note=" (sans bloc rapide, semaine de recuperation)"),
    ],
    # ===== Semaine 9 (14-20 sept) =====
    [
        ef(d(9, 0), "1/4", 45, 7.6, "6:04", "6:34"),
        {
            "date": d(9, 2), "name": "Force + Allure 42km", "difficulty": 4, "num_label": "2/4",
            "description": "15 min a 6:10-6:40/km + 14 repetitions de 30s rapide (effort libre) / 2:30 recup lente + 8 min a allure marathon 5:25/km + 3 min recup lente + 5 min recup a 6:10-6:40/km.",
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=15, pace_low="6:10", pace_high="6:40"),
                repeat(2, 14),
                step(3, StepType.interval, label="Rapide", seconds=30, parent=2),
                step(4, StepType.recovery, label="Lent", minutes=2.5, parent=2),
                step(5, StepType.interval, label="Allure marathon", minutes=8, pace_low="5:25"),
                step(6, StepType.recovery, label="Lent", minutes=3),
                step(7, StepType.cooldown, label="Recuperation", minutes=5, pace_low="6:10", pace_high="6:40"),
            ],
        },
        ef(d(9, 4), "3/4", 45, 7.6, "6:04", "6:34"),
        {
            "date": d(9, 6), "name": "Sortie Longue & Active", "difficulty": 3, "num_label": "4/4",
            "description": "Structure Tempo (non revalidee individuellement) : 75 min a 6:10-6:40/km + 25 min Tempo a 5:50/km + 10 min recup a 6:10-6:40/km.",
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=75, pace_low="6:10", pace_high="6:40"),
                step(2, StepType.interval, label="Tempo", minutes=25, pace_low="5:50"),
                step(3, StepType.cooldown, label="Recuperation", minutes=10, pace_low="6:10", pace_high="6:40"),
            ],
        },
    ],
    # ===== Semaine 10 (21-27 sept, DONNEES SOURCE INTERPOLEES) =====
    [
        ef(d(10, 0), "1/4", 45, 7.6, "6:04", "6:34", note=" (semaine dont les donnees source sont interpolees entre S9 et S11)"),
        {
            "date": d(10, 2), "name": "Force + Allure 42km", "difficulty": 4, "num_label": "2/4",
            "description": (
                "15 min a 6:08-6:38/km + 1 repetition de 30s rapide (effort libre) / 2:30 recup lente "
                "+ 12 min a allure marathon 5:24/km + 4 min recup lente + 5 min recup a 6:08-6:38/km. "
                "Decomposition litterale = 39 min ; Campus Coach affichait ~1h19 - ecart present dans la "
                "source (donnees signalees comme interpolees par l'extraction)."
            ),
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=15, pace_low="6:08", pace_high="6:38"),
                step(2, StepType.interval, label="Rapide", seconds=30),
                step(3, StepType.recovery, label="Lent", minutes=2.5),
                step(4, StepType.interval, label="Allure marathon", minutes=12, pace_low="5:24"),
                step(5, StepType.recovery, label="Lent", minutes=4),
                step(6, StepType.cooldown, label="Recuperation", minutes=5, pace_low="6:08", pace_high="6:38"),
            ],
        },
        ef(d(10, 4), "3/4", 45, 7.6, "6:04", "6:34", note=" (semaine dont les donnees source sont interpolees entre S9 et S11)"),
        {
            "date": d(10, 6), "name": "Sortie Longue & Active", "difficulty": 3, "num_label": "4/4",
            "description": "EF + 10 min Tempo a 5:39/km + 4 min SweetSpot a 5:06/km + retour EF (volume intermediaire entre S9 et S11, donnees interpolees).",
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=100, pace_low="6:08", pace_high="6:38"),
                step(2, StepType.interval, label="Tempo", minutes=10, pace_low="5:39"),
                step(3, StepType.interval, label="SweetSpot", minutes=4, pace_low="5:06"),
                step(4, StepType.cooldown, label="Recuperation", minutes=10, pace_low="6:08", pace_high="6:38"),
            ],
        },
    ],
    # ===== Semaine 11 (28 sept - 4 oct) =====
    [
        ef(d(11, 0), "1/4", 45, 7.7, "6:04", "6:34"),
        {
            "date": d(11, 2), "name": "Force + Allure 42km", "difficulty": 4, "num_label": "2/4",
            "description": (
                "15 min a 6:07-6:37/km + 2 repetitions de 30s rapide (effort libre) / 2:30 recup lente "
                "+ 20 min a allure marathon 5:24/km + 5 min recup lente + 5 min recup a 6:07-6:37/km. "
                "Decomposition litterale = 51 min ; Campus Coach affichait 1h22 - ecart present dans la source."
            ),
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=15, pace_low="6:07", pace_high="6:37"),
                repeat(2, 2),
                step(3, StepType.interval, label="Rapide", seconds=30, parent=2),
                step(4, StepType.recovery, label="Lent", minutes=2.5, parent=2),
                step(5, StepType.interval, label="Allure marathon", minutes=20, pace_low="5:24"),
                step(6, StepType.recovery, label="Lent", minutes=5),
                step(7, StepType.cooldown, label="Recuperation", minutes=5, pace_low="6:07", pace_high="6:37"),
            ],
        },
        ef(d(11, 4), "3/4", 45, 7.7, "6:04", "6:34"),
        {
            "date": d(11, 6), "name": "Sortie Longue & Active", "difficulty": 3, "num_label": "4/4",
            "description": "Pyramide progressive : 1h20 EF (6:07-6:37) + 10 min Aerobie a 5:57/km + 10 min Tempo a 5:38/km + 4 min SweetSpot a 5:06/km + 10 min Tempo a 5:38/km + 10 min Aerobie a 5:57/km + 11 min recup a 6:07-6:37/km.",
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=80, pace_low="6:07", pace_high="6:37"),
                step(2, StepType.interval, label="Aerobie", minutes=10, pace_low="5:57"),
                step(3, StepType.interval, label="Tempo", minutes=10, pace_low="5:38"),
                step(4, StepType.interval, label="SweetSpot", minutes=4, pace_low="5:06"),
                step(5, StepType.interval, label="Tempo", minutes=10, pace_low="5:38"),
                step(6, StepType.interval, label="Aerobie", minutes=10, pace_low="5:57"),
                step(7, StepType.cooldown, label="Recuperation", minutes=11, pace_low="6:07", pace_high="6:37"),
            ],
        },
    ],
    # ===== Semaine 12 (5-11 oct, RECUPERATION) =====
    [
        ef(d(12, 0), "1/4", 40, 6.8, "6:04", "6:34", note=" (semaine de recuperation)"),
        {
            "date": d(12, 2), "name": "Séance intense facile", "difficulty": 2, "num_label": "2/4",
            "description": (
                "15 min a 6:06-6:36/km + 7 repetitions de (1 min SweetSpot a 5:05/km + 1 min Seuil 30 a "
                "4:38/km) + 2 min recup lente + 10 min recup a 6:06-6:36/km. Nombre de repetitions deduit "
                "de la duree totale (~41 min)."
            ),
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=15, pace_low="6:06", pace_high="6:36"),
                repeat(2, 7),
                step(3, StepType.interval, label="SweetSpot", minutes=1, pace_low="5:05", parent=2),
                step(4, StepType.interval, label="Seuil 30", minutes=1, pace_low="4:38", parent=2),
                step(5, StepType.recovery, label="Lent", minutes=2),
                step(6, StepType.cooldown, label="Recuperation", minutes=10, pace_low="6:06", pace_high="6:36"),
            ],
        },
        ef(d(12, 4), "3/4", 45, 7.7, "6:04", "6:34", note=" (semaine de recuperation)"),
        ef(d(12, 6), "4/4", 90, 15.4, "6:06", "6:36", difficulty=2, name="Sortie Longue",
           note=" (sans bloc rapide, semaine de recuperation)"),
    ],
    # ===== Semaine 13 (12-18 oct, PIC DE CHARGE) =====
    [
        ef(d(13, 0), "1/4", 60, 10.3, "6:04", "6:34"),
        {
            "date": d(13, 2), "name": "Seuil 60", "difficulty": 5, "num_label": "2/4",
            "description": (
                "15 min EF + 8 repetitions de 3 min au seuil (~4:52-4:54/km, progression continue depuis "
                "S5) / 1:30 recup lente + 5 min recup EF. Nombre de repetitions deduit de la duree totale "
                "(58 min)."
            ),
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=15, pace_low="6:04", pace_high="6:34"),
                repeat(2, 8),
                step(3, StepType.interval, label="Seuil 60", minutes=3, pace_low="4:53", parent=2),
                step(4, StepType.recovery, label="Lent", seconds=90, parent=2),
                step(5, StepType.cooldown, label="Recuperation", minutes=5, pace_low="6:04", pace_high="6:34"),
            ],
        },
        ef(d(13, 4), "3/4", 40, 6.9, "6:04", "6:34"),
        {
            "date": d(13, 6), "name": "Long + Allure 42 km", "difficulty": 5, "num_label": "4/4",
            "description": (
                "1h10 a 6:04-6:34/km + 40 min a allure marathon 5:23/km + 2 min recup lente + 6 min recup "
                "a 6:04-6:34/km. Decomposition litterale = 1h58 ; Campus Coach affichait 2h40 - ecart present "
                "dans la source."
            ),
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=70, pace_low="6:04", pace_high="6:34"),
                step(2, StepType.interval, label="Allure marathon", minutes=40, pace_low="5:23"),
                step(3, StepType.recovery, label="Lent", minutes=2),
                step(4, StepType.cooldown, label="Recuperation", minutes=6, pace_low="6:04", pace_high="6:34"),
            ],
        },
    ],
    # ===== Semaine 14 (19-25 oct, PIC DE CHARGE) =====
    [
        ef(d(14, 0), "1/4", 45, 7.7, "6:04", "6:34"),
        {
            "date": d(14, 2), "name": "Seuil 60", "difficulty": 4, "num_label": "2/4",
            "description": "20 min a 6:04-6:34/km + 3 repetitions de 9 min a 4:50/km / 3 min recup lente + 10 min recup a 6:04-6:34/km.",
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=20, pace_low="6:04", pace_high="6:34"),
                repeat(2, 3),
                step(3, StepType.interval, label="Seuil 60", minutes=9, pace_low="4:50", parent=2),
                step(4, StepType.recovery, label="Lent", minutes=3, parent=2),
                step(5, StepType.cooldown, label="Recuperation", minutes=10, pace_low="6:04", pace_high="6:34"),
            ],
        },
        ef(d(14, 4), "3/4", 60, 10.3, "6:04", "6:34"),
        {
            "date": d(14, 6), "name": "Long + Allure 42 km", "difficulty": 4, "num_label": "4/4",
            "description": "45 min a 6:04-6:34/km + 3 blocs a allure marathon 5:23/km (10 min + 2 min recup + 30 min + 2 min recup + 10 min) + 6 min recup a 6:04-6:34/km.",
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=45, pace_low="6:04", pace_high="6:34"),
                step(2, StepType.interval, label="Allure marathon", minutes=10, pace_low="5:23"),
                step(3, StepType.recovery, label="Lent", minutes=2),
                step(4, StepType.interval, label="Allure marathon", minutes=30, pace_low="5:23"),
                step(5, StepType.recovery, label="Lent", minutes=2),
                step(6, StepType.interval, label="Allure marathon", minutes=10, pace_low="5:23"),
                step(7, StepType.cooldown, label="Recuperation", minutes=6, pace_low="6:04", pace_high="6:34"),
            ],
        },
    ],
    # ===== Semaine 15 (26 oct - 1 nov, AFFUTAGE) =====
    [
        ef(d(15, 0), "1/4", 40, 6.9, "6:04", "6:34", note=" (semaine d'affutage)"),
        {
            "date": d(15, 2), "name": "SweetSpot", "difficulty": 3, "num_label": "2/4",
            "description": (
                "36 min a 6:04-6:34/km + 6 min SweetSpot a 5:04/km + 2 min recup lente + 10 min recup a "
                "6:04-6:34/km. Echauffement etendu pour retomber sur la duree totale de 54 min affichee "
                "par Campus Coach (25 min etaient donnes dans le detail source pour ce segment)."
            ),
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=36, pace_low="6:04", pace_high="6:34"),
                step(2, StepType.interval, label="SweetSpot", minutes=6, pace_low="5:04"),
                step(3, StepType.recovery, label="Lent", minutes=2),
                step(4, StepType.cooldown, label="Recuperation", minutes=10, pace_low="6:04", pace_high="6:34"),
            ],
        },
        ef(d(15, 4), "3/4", 50, 8.6, "6:04", "6:34", note=" (semaine d'affutage)"),
        {
            "date": d(15, 6), "name": "Long + Allure 42 km", "difficulty": 4, "num_label": "4/4",
            "description": (
                "25 min a 6:04-6:34/km + 15 min a allure marathon 5:23/km + 2 min recup lente + 11 min "
                "recup a 6:04-6:34/km. Decomposition litterale = 53 min ; Campus Coach affichait 1h10 - "
                "ecart present dans la source."
            ),
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=25, pace_low="6:04", pace_high="6:34"),
                step(2, StepType.interval, label="Allure marathon", minutes=15, pace_low="5:23"),
                step(3, StepType.recovery, label="Lent", minutes=2),
                step(4, StepType.cooldown, label="Recuperation", minutes=11, pace_low="6:04", pace_high="6:34"),
            ],
        },
    ],
    # ===== Semaine 16 (2-8 nov, SEMAINE DE COURSE) =====
    [
        ef(d(16, 0), "1/4", 35, 6.0, "6:04", "6:34"),
        {
            "date": d(16, 2), "name": "Allure 42", "difficulty": 3, "num_label": "2/4",
            "description": (
                "15 min a 6:04-6:34/km + 3 min a allure marathon 5:23/km + 3 min recup lente + 5 min recup "
                "a 6:04-6:34/km. Decomposition litterale = 26 min ; Campus Coach affichait 38 min - ecart "
                "present dans la source."
            ),
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=15, pace_low="6:04", pace_high="6:34"),
                step(2, StepType.interval, label="Allure marathon", minutes=3, pace_low="5:23"),
                step(3, StepType.recovery, label="Lent", minutes=3),
                step(4, StepType.cooldown, label="Recuperation", minutes=5, pace_low="6:04", pace_high="6:34"),
            ],
        },
        {
            "date": d(16, 4), "name": "Endurance Fondamentale + Lignes droites", "difficulty": 1, "num_label": "3/4",
            "description": "15 min a 6:04-6:34/km + lignes droites (15s rapide / 45s recup lente) + 2 min recup a 6:04-6:34/km.",
            "steps": [
                step(1, StepType.warmup, label="Echauffement", minutes=15, pace_low="6:04", pace_high="6:34"),
                repeat(2, 4),
                step(3, StepType.interval, label="Ligne droite", seconds=15, parent=2),
                step(4, StepType.recovery, label="Lent", seconds=45, parent=2),
                step(5, StepType.cooldown, label="Recuperation", minutes=2, pace_low="6:04", pace_high="6:34"),
            ],
        },
        {
            "date": d(16, 6), "name": "MARATHON — Nice-Cannes", "difficulty": 5, "num_label": "4/4",
            "description": "Jour J ! Marathon Nice-Cannes, 42.2 km. Objectif allure 5:23-5:25/km (~3h48).",
            "steps": [step(1, StepType.other, label="Marathon", distance_km=42.2, pace_low="5:23", pace_high="5:25")],
        },
    ],
]


# Cible explicitement Malorie + Aureline par email : la base de prod contient aussi
# des comptes de test/anciens (Test, Lea, Lea) qui ne doivent pas recevoir ce programme.
TARGET_EMAILS = {"malorie@mimi-coach.fr", "aureline@micoach.app"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Affiche sans ecrire en base")
    args = parser.parse_args()

    init_db()

    with DBSession(engine) as db:
        athletes = db.exec(
            select(User).where(User.role == UserRole.athlete).order_by(User.id)
        ).all()
        athletes = [a for a in athletes if a.email in TARGET_EMAILS]
        found = {a.email for a in athletes}
        missing = TARGET_EMAILS - found
        if missing:
            raise SystemExit(f"Comptes cibles introuvables : {missing}")

        plan_start = d(1, 0)
        plan_end = d(16, 6)
        print(f"Plan : {plan_start} -> {plan_end} (marathon le {plan_end})\n")

        for athlete in athletes:
            print(f"=== {athlete.name} ({athlete.email}) ===")

            existing = db.exec(
                select(Session)
                .where(Session.athlete_id == athlete.id)
                .where(Session.date >= plan_start)
                .where(Session.date <= plan_end)
            ).all()
            for s in existing:
                if not args.dry_run:
                    db.delete(s)
            if existing:
                print(f"[wipe] {len(existing)} seances existantes supprimees dans la periode du plan")
            if not args.dry_run:
                db.flush()

            for week_idx, week_sessions in enumerate(WEEKS, start=1):
                week_total_min = 0
                week_total_km = 0.0
                for sdata in week_sessions:
                    session = Session(
                        athlete_id=athlete.id,
                        date=sdata["date"],
                        name=sdata["name"],
                        description=sdata.get("description"),
                        difficulty=sdata.get("difficulty", 1),
                    )
                    if not args.dry_run:
                        db.add(session)
                        db.flush()

                    id_by_marker: dict[int, int] = {}
                    steps_for_estimate: list[Step] = []
                    next_synthetic_id = 1

                    for sd in sdata["steps"]:
                        if sd.get("parent_repeat_id_marker") is None:
                            new_step = Step(
                                session_id=session.id if not args.dry_run else 0,
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
                            if not args.dry_run:
                                db.add(new_step)
                                db.flush()
                            else:
                                new_step.id = next_synthetic_id
                                next_synthetic_id += 1
                            steps_for_estimate.append(new_step)
                            if sd["kind"] == "repeat":
                                id_by_marker[sd["order"]] = new_step.id

                    for sd in sdata["steps"]:
                        marker = sd.get("parent_repeat_id_marker")
                        if marker is not None:
                            parent_id = id_by_marker.get(marker)
                            new_step = Step(
                                session_id=session.id if not args.dry_run else 0,
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
                            if not args.dry_run:
                                db.add(new_step)
                                db.flush()
                            else:
                                new_step.id = next_synthetic_id
                                next_synthetic_id += 1
                            steps_for_estimate.append(new_step)

                    duration_min, distance_km = estimate_duration_and_distance(steps_for_estimate)
                    week_total_min += duration_min
                    week_total_km += distance_km

                    if not args.dry_run:
                        db.refresh(session)
                        session.estimated_duration_min = duration_min
                        session.estimated_distance_km = distance_km
                        db.add(session)

                    print(
                        f"  [{sdata['date']}] {sdata['num_label']:>3} {sdata['name']!r:42s} "
                        f"-> {duration_min:3d} min | {distance_km:5.1f} km"
                    )

                h, m = divmod(week_total_min, 60)
                print(f"  --- Semaine {week_idx} total : {h}h{m:02d} | {week_total_km:.1f} km ---\n")

            if not args.dry_run:
                db.commit()
            print()

        if args.dry_run:
            print("DRY RUN : rien n'a ete sauvegarde.")


if __name__ == "__main__":
    main()
