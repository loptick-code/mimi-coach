"""Bibliothèque de plans et génération calée sur une date de course.

Les plans sont des modèles immuables. La date de course est la clé de placement :
la semaine finale du modèle se termine le jour de la course, puis les semaines
précédentes sont placées en remontant le calendrier. Seules les séances encore à
faire sont créées dans le compte.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

from app.services.training_paces import pace_to_seconds


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / "training_plans" / "sources"
# Les séances sont numérotées, pas prescrites par jour. La date reste un
# emplacement technique pour le calendrier et l'export; l'athlète peut faire
# chaque séance quand il le souhaite dans sa semaine.
DAY_BY_SESSION = {1: 0, 2: 1, 3: 2, 4: 6}


@dataclass
class PlanStep:
    label: str
    duration_seconds: Optional[float] = None
    distance_m: Optional[float] = None
    target_key: Optional[str] = None
    source_pace_fast: Optional[int] = None
    source_pace_slow: Optional[int] = None
    step_type: str = "interval"
    repeat_count: Optional[int] = None
    children: list["PlanStep"] = field(default_factory=list)


@dataclass
class PlanWorkout:
    week_number: int
    session_number: int
    day_offset: int
    name: str
    description: str
    difficulty: int
    steps: list[PlanStep]


@dataclass
class PlanDefinition:
    key: str
    label: str
    weeks: int
    reference_ef_seconds: int
    race_distance_m: float
    race_day_offset: int = 6
    workouts: list[PlanWorkout] = field(default_factory=list)


@dataclass
class ScheduledWorkout:
    plan_key: str
    plan_week: int
    session_number: int
    date: date
    name: str
    description: str
    difficulty: int
    steps: list[PlanStep]


_DURATION_RE = re.compile(
    r"(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>heures?|h|min(?:utes?)?|minutes?|sec(?:ondes?)?|s|km|m)(?=$|[^A-Za-zÀ-ÿ])"
    r"(?:\s*(?P<extra>\d{1,2})(?:\s*(?:min(?:utes?)?|s|sec(?:ondes?)?))?\b)?",
    re.IGNORECASE,
)
_PACE_RE = re.compile(r"(?<!\d)(\d{1,2}:\d{2})\s*(?:/\s*km)?", re.IGNORECASE)
_REPEAT_RE = re.compile(r"(?P<count>\d{1,2})\s*[x×]\s*\((?P<body>[^()]*)\)", re.IGNORECASE)
_BULLET_RE = re.compile(r"^-\s*\*\*Séance\s+(?P<number>[1-4])[^:]*:\*\*\s*(?P<body>.+)$", re.IGNORECASE)
_WEEK_RE = re.compile(r"^#{2,3}\s*Semaine\s+(?P<number>\d+)", re.IGNORECASE)


def _norm(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()


def _duration(match: re.Match[str]) -> tuple[str, float]:
    amount = float(match.group("amount").replace(",", "."))
    unit = _norm(match.group("unit"))
    extra = int(match.group("extra") or 0)
    if unit.startswith("h"):
        return "time", amount * 3600 + extra * 60
    if unit.startswith("min") or unit.startswith("minute"):
        return "time", amount * 60 + extra
    if unit in {"s", "sec", "seconde", "secondes"}:
        return "time", amount + extra
    if unit == "km":
        return "distance", amount * 1000
    return "distance", amount


def _target_key(text: str) -> Optional[str]:
    value = _norm(text)
    if re.search(r"\b(?:ef|endurance fondamentale|footing facile|facile)\b", value):
        return "ef"
    if re.search(r"\b(?:s\s*60|seuil\s*60)\b", value):
        return "s60"
    if re.search(r"\b(?:s\s*30|seuil\s*30)\b", value):
        return "s30"
    if re.search(r"\bvma\b", value):
        return "vma"
    if "sweetspot" in value or "sweet spot" in value:
        return "sweet_spot"
    if "tempo" in value:
        return "tempo"
    if "aerobie" in value:
        return "aerobic"
    if re.search(r"allure\s*10|10\s*km", value):
        return "race_10k"
    if re.search(r"allure\s*(?:semi|21)|semi[- ]marathon", value):
        return "race_half"
    if re.search(r"allure\s*(?:42|marathon)|marathon", value):
        return "race_marathon"
    return None


def _step_from_segment(segment: str) -> Optional[PlanStep]:
    match = _DURATION_RE.search(segment)
    if not match:
        return None
    duration_type, duration_value = _duration(match)
    tail = segment[match.end():]
    key = _target_key(tail) or _target_key(segment)
    paces = [pace_to_seconds(value) for value in _PACE_RE.findall(segment)]
    source_fast = min(paces) if paces else None
    source_slow = max(paces) if paces else None
    normalized = _norm(segment)
    if key in {"ef", "tempo", "s60", "s30", "vma", "sweet_spot", "aerobic", "race_10k", "race_half", "race_marathon"}:
        step_type = "interval"
    elif re.search(r"\b(?:recup|lente?|repos)\b", normalized):
        key, step_type = None, "recovery"
    elif re.search(r"\b(?:rapide|sprint|libre|lignes droites|sensations)\b", normalized):
        key, step_type = None, "interval"
    else:
        step_type = "other"
    label = tail.strip(" ,.;:") or "Course"
    if key == "ef":
        label = "EF"
    elif key:
        label = {
            "s60": "Seuil 60", "s30": "Seuil 30", "vma": "VMA", "tempo": "Tempo",
            "sweet_spot": "Sweet spot", "aerobic": "Aérobie", "race_10k": "Allure 10 km",
            "race_half": "Allure semi", "race_marathon": "Allure marathon",
        }.get(key, label)
    elif step_type == "recovery":
        label = "Récupération"
    elif re.search(r"\b(?:rapide|sprint|sensations|lignes droites)\b", normalized):
        label = "Sprint"
    return PlanStep(
        label=label[:120],
        duration_seconds=duration_value if duration_type == "time" else None,
        distance_m=duration_value if duration_type == "distance" else None,
        target_key=key,
        source_pace_fast=source_fast,
        source_pace_slow=source_slow,
        step_type=step_type,
    )


def _split_top_level(text: str) -> list[str]:
    text = re.sub(r"\b(?:puis|ensuite|et ensuite)\b", "+", text, flags=re.IGNORECASE)
    text = re.sub(r"\bet\s+(?=\d)", "+", text, flags=re.IGNORECASE)
    result: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char in "+;" and depth == 0:
            result.append(text[start:index].strip(" ,."))
            start = index + 1
        elif char == "," and depth == 0 and re.match(r"\s*(?:(?:puis|et)\s+)?\d", text[index + 1:], re.IGNORECASE):
            result.append(text[start:index].strip(" ,."))
            start = index + 1
    result.append(text[start:].strip(" ,."))
    return [part for part in result if part]


def _parse_sequence(text: str) -> list[PlanStep]:
    """Interprète les structures usuelles des sources Campus Coach.

    Les passages trop narratifs sont conservés comme une étape sans cible plutôt
    que supprimés, afin que la séance reste exportable et visible.
    """
    text = re.sub(r"\([^)]*difficulté[^)]*\)", "", text, flags=re.IGNORECASE)
    steps: list[PlanStep] = []
    cursor = 0
    for match in _REPEAT_RE.finditer(text):
        before = text[cursor:match.start()]
        steps.extend(_parse_sequence(before))
        children = _parse_sequence(match.group("body"))
        if children:
            steps.append(PlanStep(label="Bloc répété", repeat_count=int(match.group("count")), children=children))
        cursor = match.end()
    steps.extend(_parse_sequence_simple(text[cursor:]))
    return steps


def _parse_sequence_simple(text: str) -> list[PlanStep]:
    parts = _split_top_level(text)
    steps: list[PlanStep] = []
    for part in parts:
        parsed = _step_from_segment(part)
        if parsed:
            steps.append(parsed)
    if steps:
        return steps
    # Certains textes décrivent un bloc sans unité immédiatement exploitable.
    return []


def _body_after_summary(body: str) -> str:
    # Les bulletins commencent souvent par « durée, distance, difficulté. ».
    # On retire ce résumé, sans toucher aux phrases contenant les séances.
    match = re.search(r"\b(?:difficulté|difficulte)\s*\d\s*/\s*5\s*\.\s*", body, re.IGNORECASE)
    if match:
        return body[match.end():]
    summary = re.match(r"^[^.]+\bkm\s*\.\s*", body, re.IGNORECASE)
    if summary:
        return body[summary.end():]
    return body


def _source_bullets(path: Path) -> dict[int, dict[int, tuple[str, str]]]:
    weeks: dict[int, dict[int, tuple[str, str]]] = {}
    current_week: Optional[int] = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        week_match = _WEEK_RE.match(line)
        if week_match:
            current_week = int(week_match.group("number"))
            weeks.setdefault(current_week, {})
            continue
        bullet = _BULLET_RE.match(line)
        if bullet and current_week is not None:
            number = int(bullet.group("number"))
            body = bullet.group("body").strip()
            title = f"Séance {number}"
            weeks[current_week][number] = (title, _body_after_summary(body))
    return weeks


def _source_easy_table(path: Path) -> dict[int, dict[int, int]]:
    """Lit le tableau des séances 1/3 du plan semi quand il existe."""
    result: dict[int, dict[int, int]] = {}
    row = re.compile(
        r"^\|\s*(\d+)\s*\|[^|]+\|\s*(\d+)\s*min[^|]*\|\s*(\d+)\s*min[^|]*\|",
        re.IGNORECASE,
    )
    for raw in path.read_text(encoding="utf-8").splitlines():
        match = row.match(raw.strip())
        if match:
            result[int(match.group(1))] = {1: int(match.group(2)), 3: int(match.group(3))}
    return result


def _easy_workout(week: int, number: int, minutes: int, name: str = "Endurance fondamentale") -> PlanWorkout:
    return PlanWorkout(
        week_number=week, session_number=number, day_offset=DAY_BY_SESSION[number],
        name=name, description=f"{minutes} min d'endurance fondamentale.", difficulty=1,
        steps=[PlanStep(label="EF", duration_seconds=minutes * 60, target_key="ef", step_type="other")],
    )


def _source_workout(week: int, number: int, title: str, body: str, race_distance_m: float) -> PlanWorkout:
    body = re.split(r"\.\s*(?:Total|Bloc actif|Bloc facultatif|Difficulté|Difficulte)\b", body, maxsplit=1, flags=re.IGNORECASE)[0]
    steps = _parse_sequence(body)
    if not steps:
        # Le plan reste disponible même si une formulation source est narrative.
        total = _DURATION_RE.search(body)
        duration = _duration(total)[1] if total else None
        steps = [PlanStep(label=title, duration_seconds=duration, step_type="other")]
    if number == 4 and week in {13, 15, 16} and re.search(r"course|compétition|10\s*km|semi|marathon", _norm(body)):
        steps = [PlanStep(label="Course", distance_m=race_distance_m, target_key=None, step_type="other")]
    return PlanWorkout(
        week_number=week, session_number=number, day_offset=DAY_BY_SESSION[number],
        name=title, description=body[:1000], difficulty=3, steps=steps,
    )


def _load_semi() -> PlanDefinition:
    path = SOURCE_DIR / "semi_marathon_campus_coach_15_weeks.md"
    bullets = _source_bullets(path)
    easy_table = _source_easy_table(path)
    workouts: list[PlanWorkout] = []
    for week in range(1, 16):
        easy = easy_table.get(week, {1: 45, 3: 45})
        workouts.append(_easy_workout(week, 1, easy[1]))
        for number in (2, 4):
            title, body = bullets.get(week, {}).get(number, (f"Séance {number}", "45 min EF"))
            workouts.append(_source_workout(week, number, title, body, 21_100))
        workouts.append(_easy_workout(week, 3, easy[3]))
    return PlanDefinition("half", "Semi-marathon", 15, 294, 21_100, workouts=workouts)


def _load_10k() -> PlanDefinition:
    path = SOURCE_DIR / "10km_campus_coach_13_weeks.md"
    bullets = _source_bullets(path)
    workouts: list[PlanWorkout] = []
    for week in range(1, 14):
        minutes = 35 if week in {4, 9} else 45
        if week == 13:
            minutes = 20
        # La source 10 km fournie décrit les séances 2/4 et confirme que les
        # séances 1/3 sont des EF jumelles. Leur durée n'est pas explicitée,
        # donc on utilise une valeur conservative et visible dans le descriptif.
        workouts.append(_easy_workout(week, 1, minutes))
        for number in (2, 4):
            title, body = bullets.get(week, {}).get(number, (f"Séance {number}", f"{minutes} min EF"))
            workouts.append(_source_workout(week, number, title, body, 10_000))
        workouts.append(_easy_workout(week, 3, minutes))
    return PlanDefinition("10k", "10 km", 13, 294, 10_000, workouts=workouts)


def _load_marathon() -> PlanDefinition:
    # L'import historique contient les étapes complètes et leurs répétitions.
    # Il est chargé paresseusement pour éviter d'exécuter son main().
    from scripts.import_marathon_16w import WEEKS

    workouts: list[PlanWorkout] = []
    for week_number, week in enumerate(WEEKS, start=1):
        for item in week:
            match = re.search(r"([1-4])\s*/\s*4", str(item.get("num_label", "")))
            number = int(match.group(1)) if match else min(len([w for w in workouts if w.week_number == week_number]) + 1, 4)
            converted: list[PlanStep] = []
            for source in item.get("steps", []):
                duration_value = source.get("duration_value")
                duration_type = str(source.get("duration_type", ""))
                duration_seconds = float(duration_value) if duration_value is not None and "time" in duration_type else None
                distance_m = float(duration_value) if duration_value is not None and "distance" in duration_type else None
                low = source.get("target_low")
                high = source.get("target_high")
                source_fast = round(1000 / high) if high else None
                source_slow = round(1000 / low) if low else None
                label = str(source.get("label") or "Course")
                key = _target_key(label)
                if key is None and label.lower() in {"lent", "recuperation", "récupération"}:
                    step_type = "recovery"
                else:
                    step_type = str(source.get("type", "interval")).split(".")[-1].lower()
                converted.append(PlanStep(
                    label=label, duration_seconds=duration_seconds, distance_m=distance_m,
                    target_key=key, source_pace_fast=source_fast, source_pace_slow=source_slow,
                    step_type=step_type, repeat_count=source.get("repeat_count"),
                ))
            workouts.append(PlanWorkout(
                week_number=week_number, session_number=number, day_offset=(number - 1) * 2,
                name=str(item.get("name") or f"Séance {number}"),
                description=str(item.get("description") or ""),
                difficulty=int(item.get("difficulty") or 1), steps=converted,
            ))
    return PlanDefinition("marathon", "Marathon", 16, 379, 42_195, workouts=workouts)


_PLAN_CACHE: dict[str, PlanDefinition] = {}


def get_plan(goal: str) -> PlanDefinition:
    if goal not in _PLAN_CACHE:
        _PLAN_CACHE[goal] = {"10k": _load_10k, "half": _load_semi, "marathon": _load_marathon}[goal]()
    return _PLAN_CACHE[goal]


def _scale_step(step: PlanStep, factor: float, ef_fast: int, ef_slow: int) -> PlanStep:
    result = PlanStep(
        label=step.label, duration_seconds=step.duration_seconds, distance_m=step.distance_m,
        target_key=step.target_key, step_type=step.step_type, repeat_count=step.repeat_count,
        children=[_scale_step(child, factor, ef_fast, ef_slow) for child in step.children],
    )
    if step.target_key == "ef":
        result.source_pace_fast, result.source_pace_slow = ef_fast, ef_slow
    elif step.source_pace_fast is not None:
        result.source_pace_fast = round(step.source_pace_fast * factor)
        result.source_pace_slow = round((step.source_pace_slow or step.source_pace_fast) * factor)
    return result


def generate_schedule(
    goal: str,
    race_date: date,
    ef_fast_seconds: int,
    ef_slow_seconds: int,
    sessions_per_week: int,
    *,
    today: Optional[date] = None,
) -> tuple[PlanDefinition, list[ScheduledWorkout]]:
    if sessions_per_week not in (3, 4):
        raise ValueError("Le nombre de séances doit être 3 ou 4.")
    if race_date < (today or date.today()):
        raise ValueError("La date de course doit être aujourd'hui ou dans le futur.")
    plan = get_plan(goal)
    reference = plan.reference_ef_seconds
    factor = ((ef_fast_seconds + ef_slow_seconds) / 2) / reference
    final_monday = race_date - timedelta(days=plan.race_day_offset)
    first_monday = final_monday - timedelta(weeks=plan.weeks - 1)
    earliest = today or date.today()
    scheduled: list[ScheduledWorkout] = []
    for workout in plan.workouts:
        if sessions_per_week == 3 and workout.session_number == 3:
            continue
        workout_date = first_monday + timedelta(weeks=workout.week_number - 1, days=workout.day_offset)
        if workout_date < earliest or workout_date > race_date:
            continue
        scheduled.append(ScheduledWorkout(
            plan_key=goal, plan_week=workout.week_number, session_number=workout.session_number,
            date=workout_date, name=workout.name, description=workout.description,
            difficulty=workout.difficulty,
            steps=[_scale_step(step, factor, ef_fast_seconds, ef_slow_seconds) for step in workout.steps],
        ))
    scheduled.sort(key=lambda item: (item.date, item.session_number))
    return plan, scheduled
