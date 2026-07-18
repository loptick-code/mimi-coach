"""Configuration centralisee des templates Jinja2."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


# --- Filtres custom ---


def format_pace_m_per_s(speed: float | None) -> str:
    """Convertit une vitesse en m/s en allure 'mm:ss/km'."""
    if not speed or speed <= 0:
        return "-"
    seconds_per_km = 1000.0 / speed
    minutes = int(seconds_per_km // 60)
    seconds = int(round(seconds_per_km - minutes * 60))
    if seconds == 60:
        minutes += 1
        seconds = 0
    return f"{minutes}:{seconds:02d}/km"


def format_duration_seconds(seconds: float | None) -> str:
    if not seconds:
        return "-"
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}"
    return f"{m} min" if s == 0 else f"{m}'{s:02d}\""


def format_distance_meters(meters: float | None) -> str:
    if not meters:
        return "-"
    if meters >= 1000:
        return f"{meters / 1000:.1f} km"
    return f"{int(meters)} m"


templates.env.filters["pace"] = format_pace_m_per_s
templates.env.filters["duration"] = format_duration_seconds
templates.env.filters["distance"] = format_distance_meters


# --- Globals ---


def is_athlete2(user) -> bool:
    """True si le user connecte est le 2eme athlete (cf ATHLETE2_EMAIL)."""
    from app.config import get_settings

    settings = get_settings()
    return bool(
        user is not None
        and settings.athlete2_email
        and (user.email or "").lower() == settings.athlete2_email.lower()
    )


templates.env.globals["is_athlete2"] = is_athlete2
