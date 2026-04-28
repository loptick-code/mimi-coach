"""Wrapper autour de la librairie garminconnect.

Responsabilites :
- Login initial (email + password) -> renvoie un token serialise
- Stockage chiffre du token via app.crypto
- Re-authentification depuis le token stocke (sans mot de passe)
- Push d'une seance (RunningWorkout) vers le compte Garmin
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any, Callable, Optional

from garminconnect import Garmin
from garminconnect.workout import RunningWorkout

from app.crypto import decrypt, encrypt


class GarminError(RuntimeError):
    """Erreur d'integration Garmin (auth, push, etc.)."""


class GarminMFARequired(GarminError):
    """L'utilisateur a la 2FA active - on ne supporte pas le MFA pour l'instant."""


@dataclass
class GarminLoginResult:
    """Resultat d'un login : token chiffre + nom affichable."""

    token_encrypted: bytes
    display_name: str | None


def login_with_credentials(email: str, password: str) -> GarminLoginResult:
    """Realise un login natif Garmin et renvoie le token chiffre.

    Leve GarminMFARequired si la 2FA est active (cas pas geree pour l'instant).
    """
    client = Garmin(email=email, password=password, return_on_mfa=True)
    try:
        mfa_status, _ = client.login()
    except Exception as exc:  # pragma: no cover - re-emis tel quel
        raise GarminError(f"Echec de connexion a Garmin : {exc}") from exc

    if mfa_status == "needs_mfa":
        raise GarminMFARequired(
            "Ton compte Garmin a la 2FA activee. Cette fonctionnalite "
            "n'est pas encore supportee."
        )

    token_str = client.client.dumps()
    if not token_str or len(token_str) <= 512:
        raise GarminError(
            "Token Garmin invalide apres login. Reessaye dans quelques instants."
        )

    display_name = _safe_display_name(client)
    return GarminLoginResult(
        token_encrypted=encrypt(token_str),
        display_name=display_name,
    )


def resume_client(token_encrypted: bytes) -> Garmin:
    """Recharge un client Garmin pret a l'usage depuis un token chiffre."""
    token_str = decrypt(token_encrypted)
    client = Garmin()
    try:
        # Si tokenstore > 512 chars, login() le charge comme string
        client.login(tokenstore=token_str)
    except Exception as exc:
        raise GarminError(
            f"Token Garmin expire ou invalide ({exc}). "
            "Reconnecte ton compte Garmin dans les parametres."
        ) from exc
    return client


def push_running_workout(
    token_encrypted: bytes,
    workout: RunningWorkout,
    on_token_refreshed: Callable[[bytes], None] | None = None,
) -> int:
    """Push une seance running et renvoie le workoutId Garmin.

    Si le token a ete refresh pendant l'appel, on rappelle on_token_refreshed
    avec le nouveau token chiffre pour persistance.
    """
    client = resume_client(token_encrypted)

    try:
        result: dict[str, Any] = client.upload_running_workout(workout)
    except Exception as exc:
        raise GarminError(f"Echec du push de la seance : {exc}") from exc

    workout_id = result.get("workoutId") if isinstance(result, dict) else None
    if not workout_id:
        raise GarminError(
            f"Reponse Garmin sans workoutId : {result!r}"
        )

    # Si le token a ete refresh, le persister
    if on_token_refreshed is not None:
        new_token = client.client.dumps()
        if new_token and len(new_token) > 512:
            on_token_refreshed(encrypt(new_token))

    return int(workout_id)


@dataclass
class GarminActivitySummary:
    """Resume d'une activite Garmin completee (vue depuis l'app)."""

    activity_id: int
    duration_sec: int
    distance_m: float
    start_time: Optional[_dt.datetime]
    activity_type: str
    name: str = ""


_RUNNING_TYPES = {
    "running",
    "trail_running",
    "indoor_running",
    "treadmill_running",
    "track_running",
    "street_running",
    "virtual_run",
}


def _activity_dict_to_summary(a: dict) -> Optional["GarminActivitySummary"]:
    """Convertit un dict d'activite Garmin en GarminActivitySummary, ou None si invalide / pas un run."""
    type_key = (a.get("activityType") or {}).get("typeKey", "").lower()
    if type_key not in _RUNNING_TYPES:
        return None
    activity_id = a.get("activityId")
    duration = a.get("duration")
    distance = a.get("distance")
    if not activity_id or not duration or not distance:
        return None
    start_str = a.get("startTimeLocal") or a.get("startTimeGMT")
    start_dt = None
    if start_str:
        try:
            start_dt = _dt.datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        except ValueError:
            pass
    return GarminActivitySummary(
        activity_id=int(activity_id),
        duration_sec=int(duration),
        distance_m=float(distance),
        start_time=start_dt,
        activity_type=type_key,
        name=a.get("activityName") or "",
    )


def fetch_recent_running_activities(
    token_encrypted: bytes,
    limit: int = 30,
    on_token_refreshed: Callable[[bytes], None] | None = None,
) -> list["GarminActivitySummary"]:
    """Recupere les `limit` activites running les plus recentes du compte Garmin.

    Pas de filtre par date - retourne toutes les courses recentes pour que
    l'utilisateur puisse choisir laquelle assigner a la seance.
    """
    client = resume_client(token_encrypted)
    try:
        # get_activities(start, limit) renvoie les `limit` activites a partir de l'index `start`
        activities = client.get_activities(0, limit)
    except Exception as exc:
        raise GarminError(f"Echec de la recuperation des activites : {exc}") from exc

    if on_token_refreshed is not None:
        new_token = client.client.dumps()
        if new_token and len(new_token) > 512:
            on_token_refreshed(encrypt(new_token))

    if not activities:
        return []

    summaries: list[GarminActivitySummary] = []
    for a in activities:
        s = _activity_dict_to_summary(a)
        if s is not None:
            summaries.append(s)

    # Tri par date descendante (plus recent en premier)
    summaries.sort(
        key=lambda x: x.start_time or _dt.datetime.min,
        reverse=True,
    )
    return summaries


def _safe_display_name(client: Garmin) -> str | None:
    """Tente de recuperer un nom affichable, sans planter sur erreur reseau."""
    try:
        full = client.get_full_name()
        if full:
            return str(full)
    except Exception:
        pass
    try:
        return getattr(client, "display_name", None) or getattr(client, "username", None)
    except Exception:
        return None
