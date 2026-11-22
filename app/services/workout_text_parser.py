"""Parseur sans IA pour les séances Mi Coach, à syntaxe volontairement cadrée."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional


DAY_INDEX = {
    "lundi": 0, "lun": 0,
    "mardi": 1, "mar": 1,
    "mercredi": 2, "mer": 2,
    "jeudi": 3, "jeu": 3,
    "vendredi": 4, "ven": 4,
    "samedi": 5, "sam": 5,
    "dimanche": 6, "dim": 6,
}
DAY_LABELS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")

ZONE_ALIASES = (
    ("race_10k", re.compile(r"\b(?:allure\s*)?10\s*km\b")),
    ("race_half", re.compile(r"\b(?:allure\s*)?(?:semi(?:-marathon)?|21\s*km)\b")),
    ("race_marathon", re.compile(r"\b(?:allure\s*)?marathon\b")),
    ("sweet_spot", re.compile(r"\bsweet\s*spot\b")),
    ("s60", re.compile(r"\b(?:s\s*60|seuil\s*60)\b")),
    ("s30", re.compile(r"\b(?:s\s*30|seuil\s*30)\b")),
    ("vma", re.compile(r"\bvma\b")),
    ("tempo", re.compile(r"\btempo\b")),
    ("aerobic", re.compile(r"\baerobie\b")),
    ("ef", re.compile(r"\b(?:ef|endurance fondamentale|footing facile|footing facile|facile)\b")),
)
PACE_RE = re.compile(r"(?:\ba\s*|@\s*)?(\d{1,2}:\d{2})\s*(?:/\s*km)?\b", re.IGNORECASE)
PACE_RANGE_RE = re.compile(r"(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})\s*(?:/\s*km)?", re.IGNORECASE)
REPEAT_RE = re.compile(r"^(\d{1,2})\s*[x×]\s*\((.*)\)$", re.IGNORECASE)
DURATION_RE = re.compile(
    r"^\s*(?P<amount>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>heures?|h|min(?:utes?)?|minutes?|sec(?:ondes?)?|s|km|m)(?=[^a-z]|$)"
    r"(?:\s*(?P<extra>\d{1,2})(?:\s*(?:min(?:utes?)?|s|sec(?:ondes?)?))?\b)?\s*"
    r"(?P<rest>.*?)\s*$",
    re.IGNORECASE,
)
SESSION_RE = re.compile(
    r"^(?:seance\s+(?P<session_number>\d+)\s*\((?P<session_day>[^)]+)\)|(?P<day>[a-z]+))\s*:\s*(?P<body>.+)$",
    re.IGNORECASE,
)
WEEK_RE = re.compile(r"^semaine\s+(\d+)\s*$", re.IGNORECASE)


@dataclass
class ParsedStep:
    kind: str = "step"
    step_type: str = "interval"
    label: str = ""
    duration_type: str = "time"
    duration_value: float = 0
    target_type: str = "no_target"
    pace_fast_seconds_per_km: Optional[int] = None
    pace_slow_seconds_per_km: Optional[int] = None
    repeat_count: Optional[int] = None
    parent_repeat_index: Optional[int] = None


@dataclass
class ParsedWorkout:
    week_number: int
    weekday: int
    weekday_label: str
    source_order: int
    name: str
    steps: list[ParsedStep]
    session_number: Optional[int] = None
    date: Optional[date] = None


@dataclass
class ParseResult:
    workouts: list[ParsedWorkout]
    omitted_workouts: list[ParsedWorkout]


class WorkoutTextError(ValueError):
    pass


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", value.strip().lower())


def _pace_seconds(value: str) -> int:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value)
    if not match or int(match.group(2)) >= 60:
        raise WorkoutTextError(f"Allure invalide : {value}. Utilise le format 4:30/km.")
    return int(match.group(1)) * 60 + int(match.group(2))


def _split_plus(expression: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for idx, char in enumerate(expression):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise WorkoutTextError("Parenthèses non équilibrées dans le bloc répété.")
        elif char == "+" and depth == 0:
            parts.append(expression[start:idx].strip())
            start = idx + 1
    if depth != 0:
        raise WorkoutTextError("Parenthèses non équilibrées dans le bloc répété.")
    parts.append(expression[start:].strip())
    return [part for part in parts if part]


def _zone(rest: str) -> tuple[Optional[str], str]:
    normalized = _norm(rest)
    for key, pattern in ZONE_ALIASES:
        match = pattern.search(normalized)
        if match:
            label = {
                "ef": "EF", "s60": "Seuil 60", "s30": "Seuil 30", "vma": "VMA",
                "tempo": "Tempo", "sweet_spot": "Sweet spot", "aerobic": "Aérobie",
                "race_10k": "Allure 10 km", "race_half": "Allure semi", "race_marathon": "Allure marathon",
            }[key]
            return key, label
    if re.search(r"\b(?:recup(?:eration)?|lent|lente|repos)\b", normalized):
        return "recovery", "Récupération"
    if re.search(r"\b(?:echauffement|warm ?up)\b", normalized):
        return "warmup", "Échauffement"
    if re.search(r"\b(?:retour au calme|rc|cool ?down)\b", normalized):
        return "cooldown", "Retour au calme"
    if re.search(r"\b(?:rapide|sprint|lignes droites|aux sensations)\b", normalized):
        return "fast_by_feel", "Rapide, aux sensations"
    return None, ""


def _parse_one_step(
    text: str,
    pace_targets: dict[str, int],
    ef_range: Optional[tuple[int, int]],
) -> ParsedStep:
    match = DURATION_RE.match(text)
    if not match:
        raise WorkoutTextError(
            f"Étape non reconnue : « {text} ». Commence par une durée ou distance, par exemple « 40 min EF »."
        )
    amount = float(match.group("amount").replace(",", "."))
    unit = _norm(match.group("unit"))
    extra = int(match.group("extra") or 0)
    rest = match.group("rest") or ""
    if amount <= 0:
        raise WorkoutTextError("La durée ou la distance doit être supérieure à zéro.")

    if unit.startswith("h"):
        duration_type, duration_value = "time", amount * 3600 + extra * 60
    elif unit.startswith("min") or unit.startswith("minute"):
        duration_type, duration_value = "time", amount * 60 + extra
    elif unit in ("s", "sec", "seconde", "secondes"):
        duration_type, duration_value = "time", amount + extra
    elif unit == "km":
        duration_type, duration_value = "distance", amount * 1000
    else:
        duration_type, duration_value = "distance", amount

    pace_range = PACE_RANGE_RE.search(rest)
    explicit_pace = PACE_RE.search(rest) if not pace_range else None
    if pace_range:
        first = _pace_seconds(pace_range.group(1))
        second = _pace_seconds(pace_range.group(2))
        pace_fast, pace_slow = min(first, second), max(first, second)
    elif explicit_pace:
        pace_fast = pace_slow = _pace_seconds(explicit_pace.group(1))
    else:
        pace_fast = pace_slow = None

    key, label = _zone(rest)
    if key is None and (pace_fast is not None):
        key, label = "custom", "Allure personnalisée"
    if key is None:
        raise WorkoutTextError(f"Type d'effort non reconnu dans « {text} ». Utilise EF, S60, S30, VMA, tempo, récupération ou une allure explicite.")

    step_type = "interval"
    if key in ("ef", "custom"):
        step_type = "other"
    elif key == "recovery":
        step_type = "recovery"
    elif key == "warmup":
        step_type = "warmup"
    elif key == "cooldown":
        step_type = "cooldown"

    target_type = "no_target"
    if key == "ef" and pace_fast is None:
        if ef_range is not None:
            pace_fast, pace_slow = ef_range
            target_type = "pace"
    elif key in pace_targets and pace_fast is None:
        pace_fast = pace_slow = pace_targets[key]
        target_type = "pace"
    elif key == "custom" or pace_fast is not None:
        target_type = "pace"
    elif key == "race_marathon" and pace_fast is None:
        if key in pace_targets:
            pace_fast = pace_slow = pace_targets[key]
            target_type = "pace"
        else:
            raise WorkoutTextError("L'allure marathon doit être saisie explicitement ou configurée dans le profil.")
    elif key in ("s60", "s30", "vma", "tempo", "sweet_spot", "aerobic", "race_10k", "race_half"):
        raise WorkoutTextError(f"L'allure {label} n'est pas configurée. Renseigne-la dans les paramètres ou indique-la, par exemple « à 4:30/km ».")

    if key == "ef" and target_type == "no_target":
        label = "EF"
    return ParsedStep(
        step_type=step_type,
        label=label,
        duration_type=duration_type,
        duration_value=duration_value,
        target_type=target_type,
        pace_fast_seconds_per_km=pace_fast,
        pace_slow_seconds_per_km=pace_slow,
    )


def _parse_expression(
    expression: str,
    pace_targets: dict[str, int],
    ef_range: Optional[tuple[int, int]],
    allow_repeat: bool = True,
) -> list[ParsedStep]:
    result: list[ParsedStep] = []
    for part in _split_plus(expression):
        repeat = REPEAT_RE.fullmatch(part)
        if repeat:
            if not allow_repeat:
                raise WorkoutTextError("Les blocs répétés imbriqués ne sont pas pris en charge.")
            count = int(repeat.group(1))
            if count < 1 or count > 50:
                raise WorkoutTextError("Le nombre de répétitions doit être compris entre 1 et 50.")
            parent_index = len(result)
            result.append(ParsedStep(kind="repeat", repeat_count=count))
            children = _parse_expression(repeat.group(2), pace_targets, ef_range, allow_repeat=False)
            if not children:
                raise WorkoutTextError("Le bloc répété est vide.")
            for child in children:
                child.parent_repeat_index = parent_index
                result.append(child)
            continue
        if "(" in part or ")" in part:
            raise WorkoutTextError("Utilise le format « 6 x (3 min S60 + 1 min 30 récupération) » pour un bloc répété.")
        result.append(_parse_one_step(part, pace_targets, ef_range))
    return result


def parse_workout_text(
    text: str,
    *,
    start_date: date,
    pace_targets: Optional[dict[str, int]] = None,
    ef_range: Optional[tuple[int, int]] = None,
    sessions_per_week: int = 4,
) -> ParseResult:
    """Parse les séances par ligne. Le texte ne peut créer aucun objet en base."""
    if sessions_per_week not in (3, 4):
        raise WorkoutTextError("Choisis 3 ou 4 séances par semaine dans les paramètres.")
    pace_targets = pace_targets or {}
    current_week = 1
    workouts: list[ParsedWorkout] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        normalized = _norm(line)
        week_match = WEEK_RE.fullmatch(normalized)
        if week_match:
            current_week = int(week_match.group(1))
            if current_week < 1 or current_week > 60:
                raise WorkoutTextError(f"Ligne {line_number} : le numéro de semaine doit être entre 1 et 60.")
            continue
        match = SESSION_RE.fullmatch(normalized)
        if not match:
            raise WorkoutTextError(
                f"Ligne {line_number} non reconnue : « {line} ». Utilise « Semaine 1 », puis « Lundi : 40 min EF »."
            )
        day_token = match.group("session_day") or match.group("day") or ""
        weekday = DAY_INDEX.get(_norm(day_token))
        if weekday is None:
            raise WorkoutTextError(f"Ligne {line_number} : jour non reconnu (« {day_token} »).")
        # The normalized line is used intentionally so parser behavior is accent-insensitive.
        body = match.group("body")
        steps = _parse_expression(body, pace_targets, ef_range)
        if not steps:
            raise WorkoutTextError(f"Ligne {line_number} : la séance ne contient aucune étape.")
        effort = next(
            (
                step.label
                for step in steps
                if step.kind != "repeat"
                and step.label
                and step.step_type not in ("warmup", "recovery", "cooldown")
            ),
            "Course",
        )
        source_order = len(workouts)
        session_number = int(match.group("session_number")) if match.group("session_number") else None
        week_start = start_date + timedelta(days=(current_week - 1) * 7)
        workouts.append(
            ParsedWorkout(
                week_number=current_week,
                weekday=weekday,
                weekday_label=DAY_LABELS[weekday],
                source_order=source_order,
                name=f"{effort} · semaine {current_week}",
                steps=steps,
                session_number=session_number,
                date=week_start + timedelta(days=weekday),
            )
        )
    if not workouts:
        raise WorkoutTextError("Aucune séance reconnue. Ajoute des lignes du type « Lundi : 40 min EF ».")

    by_week: dict[int, list[ParsedWorkout]] = {}
    for workout in workouts:
        by_week.setdefault(workout.week_number, []).append(workout)
    omitted: list[ParsedWorkout] = []
    for week_number, week_workouts in by_week.items():
        if len(week_workouts) > 4:
            raise WorkoutTextError(f"La semaine {week_number} contient plus de 4 séances, ce qui n'est pas pris en charge.")
        if sessions_per_week == 3:
            explicitly_numbered = all(workout.session_number is not None for workout in week_workouts)
            numbered_third = [workout for workout in week_workouts if workout.session_number == 3]
            if numbered_third:
                # Une semaine de course peut ne contenir que les séances 2, 3 et 4.
                omitted.extend(numbered_third)
            elif not explicitly_numbered and len(week_workouts) == 4:
                # Sans numéros explicites, le troisième élément d'un plan à quatre séances est retiré.
                omitted.append(week_workouts[2])
    omitted_ids = {id(workout) for workout in omitted}
    kept = [workout for workout in workouts if id(workout) not in omitted_ids]
    return ParseResult(workouts=kept, omitted_workouts=omitted)
