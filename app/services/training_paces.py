"""Calcul déterministe des allures depuis une plage EF de 30 secondes/km."""
from __future__ import annotations

import re
from typing import Optional

# Allures de référence extraites des plans Campus Coach transmis.
# Les allures EF des plans 10 km et semi ont une plage de 30 s/km centrée sur 4:54.
REFERENCE_EF_SECONDS_PER_KM = 4 * 60 + 54
PACE_REFERENCE_BY_GOAL: dict[str, dict[str, int]] = {
    "10k": {
        "s60": 3 * 60 + 36,
        "s30": 3 * 60 + 24,
        "vma": 2 * 60 + 57,
        "tempo": 4 * 60 + 15,
        "sweet_spot": 3 * 60 + 47,
        "aerobic": 4 * 60 + 28,
        "race_10k": 3 * 60 + 22,
    },
    "half": {
        "s60": 3 * 60 + 35,
        "s30": 3 * 60 + 24,
        "vma": 2 * 60 + 58,
        "tempo": 4 * 60 + 14,
        "sweet_spot": 3 * 60 + 46,
        "race_half": 3 * 60 + 33,
    },
}

PACE_LABELS: dict[str, str] = {
    "s60": "Seuil 60",
    "s30": "Seuil 30",
    "vma": "VMA",
    "tempo": "Tempo",
    "sweet_spot": "Sweet spot",
    "aerobic": "Aérobie",
    "race_10k": "Allure 10 km",
    "race_half": "Allure semi",
    "race_marathon": "Allure marathon",
}


def pace_to_seconds(value: str) -> int:
    """Parse une allure M:SS[/km] en secondes par km."""
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*(?:/\s*km)?\s*", value, re.IGNORECASE)
    if not match:
        raise ValueError("Allure invalide, format attendu : 5:15/km")
    minutes, seconds = int(match.group(1)), int(match.group(2))
    if seconds >= 60 or minutes <= 0:
        raise ValueError("Allure invalide")
    return minutes * 60 + seconds


def seconds_to_pace(value: Optional[int]) -> str:
    if value is None or value <= 0:
        return ""
    minutes, seconds = divmod(int(value), 60)
    return f"{minutes}:{seconds:02d}"


def validate_ef_range(fast_seconds: int, slow_seconds: int) -> None:
    if fast_seconds <= 0 or slow_seconds <= 0:
        raise ValueError("Les deux bornes de l'EF doivent être renseignées")
    if slow_seconds <= fast_seconds:
        raise ValueError("La borne lente de l'EF doit être plus lente que la borne rapide")
    if slow_seconds - fast_seconds != 30:
        raise ValueError("La plage EF doit couvrir exactement 30 secondes par km")


def calculate_pace_targets(
    goal: str, ef_fast_seconds: int, ef_slow_seconds: int
) -> dict[str, int]:
    """Calcule les allures auto depuis le milieu de la plage EF.

    Les objectifs marathon n'ont pas encore de référentiel neutre validé, donc
    la fonction renvoie un dictionnaire vide pour ce cas.
    """
    validate_ef_range(ef_fast_seconds, ef_slow_seconds)
    reference = PACE_REFERENCE_BY_GOAL.get(goal)
    if not reference:
        return {}
    ef_midpoint = (ef_fast_seconds + ef_slow_seconds) / 2
    factor = ef_midpoint / REFERENCE_EF_SECONDS_PER_KM
    return {key: int(round(seconds * factor)) for key, seconds in reference.items()}


def supported_goal(goal: str) -> bool:
    return goal in {"10k", "half", "marathon"}
