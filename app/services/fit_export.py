"""Fallback FIT export.

L'API Python de garmin_fit_sdk (version 21.x) ne permet pas l'encodage haut
niveau de fichiers .FIT workout - elle est principalement orientee decodage.

Pour l'instant, le fallback expose le JSON workout (le meme que celui envoye
a Garmin Connect par push_running_workout). Ce JSON peut etre utilise via
des outils tiers ou par debug.

Pour un vrai .FIT workout exportable, on pourrait integrer la librairie
`fit-tool` (https://github.com/mtucker6784/fit_tool) dans une iteration future.
"""

from __future__ import annotations

import json

from app.models import Session as DBSession
from app.services.workout_builder import session_to_running_workout


def session_to_workout_json(session: DBSession) -> bytes:
    """Renvoie le JSON workout (format Garmin Connect) pret a etre telecharge."""
    rw = session_to_running_workout(session)
    payload = rw.to_dict() if hasattr(rw, "to_dict") else rw.model_dump(by_alias=True)
    return json.dumps(payload, indent=2, default=str, ensure_ascii=False).encode("utf-8")
