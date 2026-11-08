"""Modeles SQLModel.

Modele simple inspire de Campus Coach :
- User : un compte distinct par personne, avec les mêmes droits
- Session : une seance d'entrainement programmee a une date
- Step : une etape d'une seance (echauffement, intervalle, recup, retour calme)
- RepeatGroup : un groupe d'etapes a repeter N fois (ex : 10 x 1' seuil + 1' lent)

Le modele de step est lineaire avec un champ `parent_repeat_id` qui pointe
vers une ligne de type "repeat" pour materialiser les blocs repetes.
"""

import datetime as _dt
from enum import Enum
from typing import List, Optional

from sqlalchemy import BigInteger, Column
from sqlmodel import Field, Relationship, SQLModel


# --- Enums ---


class UserRole(str, Enum):
    """Valeurs conservées pour compatibilité avec le schéma historique."""

    coach = "coach"
    athlete = "athlete"


class StepType(str, Enum):
    """Type d'etape - reflete les categories Campus Coach + Garmin."""

    warmup = "warmup"  # ECHAUFFEMENT
    interval = "interval"  # bloc d'effort (Seuil, VMA, etc)
    recovery = "recovery"  # recup entre intervalles
    cooldown = "cooldown"  # RETOUR CALME / RECUPERATION finale
    other = "other"  # course continue libre


class DurationType(str, Enum):
    time = "time"  # secondes
    distance = "distance"  # metres
    lap_button = "lap_button"  # appui sur le bouton lap


class TargetType(str, Enum):
    no_target = "no_target"
    pace = "pace"  # m/s, plage min-max
    heart_rate = "heart_rate"  # bpm, plage min-max


class SessionFeedback(str, Enum):
    """Ressenti apres une seance, alimente l'adaptation auto."""

    too_easy = "too_easy"
    perfect = "perfect"
    too_hard = "too_hard"


# Effet de chaque feedback sur le pace_adjustment_pct (cumulatif, borne +-10)
FEEDBACK_DELTA = {
    SessionFeedback.too_easy: -2.0,
    SessionFeedback.perfect: 0.0,
    SessionFeedback.too_hard: +2.0,
}
PACE_ADJUSTMENT_BOUND = 10.0  # %


# --- Tables ---


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    name: str
    password_hash: str
    # Champ historique conservé pour compatibilité DB. Les accès sont basés
    # sur la propriété des séances, pas sur ce rôle.
    role: UserRole = Field(default=UserRole.athlete)

    # Token SSO Garmin chiffre (Fernet) : dump JSON complet du client garminconnect
    # (Client.dumps(), format lie a la version de la lib - voir garminconnect==X.Y.Z
    # dans pyproject.toml/requirements.txt. Un changement de version peut rendre les
    # tokens stockes illisibles ; il faut alors reconnecter le compte Garmin).
    garmin_token_encrypted: Optional[bytes] = Field(default=None)
    garmin_connected_name: Optional[str] = Field(
        default=None,
        description="Nom de l'utilisateur Garmin connecte, affiche dans la UI",
    )

    # Adaptation auto de l'allure :
    # +x => allures plus lentes (paces min/km plus elevees) ; -x => plus rapides
    # Borne +/-10. Ne s'applique qu'aux seances NON encore validees.
    pace_adjustment_pct: float = Field(default=0.0)

    sessions: List["Session"] = Relationship(
        back_populates="athlete",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class Session(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    athlete_id: int = Field(foreign_key="user.id", index=True)
    date: _dt.date = Field(index=True)
    name: str  # ex: "Endurance Fondamentale", "Sortie Longue"
    description: Optional[str] = None  # notes sur la séance
    difficulty: int = Field(default=1, ge=1, le=4)  # eclairs 1-4

    # Estimations cachees (calculees par workout_builder, persistees pour la liste)
    estimated_duration_min: Optional[int] = None
    estimated_distance_km: Optional[float] = None

    # Push Garmin
    garmin_workout_id: Optional[int] = Field(default=None, index=True)
    pushed_at: Optional[_dt.datetime] = None
    last_push_error: Optional[str] = None

    # Validation / ressenti
    feedback: Optional[SessionFeedback] = Field(default=None)
    validated_at: Optional[_dt.datetime] = Field(default=None)
    # Snapshot du pace_adjustment_pct au moment de la validation (pour gel)
    pace_adjustment_at_validation: Optional[float] = Field(default=None)

    # Realise (recupere depuis Garmin a la validation)
    # Note : Garmin activity IDs depassent souvent 2^31 (4 milliards+),
    # il FAUT BigInteger sinon Postgres overflow sur INTEGER 32 bits.
    garmin_activity_id: Optional[int] = Field(
        default=None,
        sa_column=Column("garmin_activity_id", BigInteger, nullable=True, index=True),
    )
    actual_duration_sec: Optional[int] = Field(default=None)
    actual_distance_m: Optional[float] = Field(default=None)

    created_at: _dt.datetime = Field(default_factory=_dt.datetime.utcnow)
    updated_at: _dt.datetime = Field(default_factory=_dt.datetime.utcnow)

    athlete: Optional[User] = Relationship(back_populates="sessions")
    steps: List["Step"] = Relationship(
        back_populates="session",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "Step.order",
        },
    )


class Step(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    order: int = Field(index=True)

    # null = etape simple ; non-null = enfant d'un groupe a repeter (parent.kind=="repeat")
    parent_repeat_id: Optional[int] = Field(
        default=None,
        foreign_key="step.id",
    )

    # "step" = etape executable, "repeat" = groupe a repeter (sans duration/target)
    kind: str = Field(default="step")  # "step" | "repeat"
    repeat_count: Optional[int] = Field(default=None)  # uniquement si kind="repeat"

    type: StepType = Field(default=StepType.interval)
    label: Optional[str] = None  # "EF", "Seuil 60", "VMA"

    duration_type: DurationType = Field(default=DurationType.time)
    # secondes (time) ou metres (distance) ; ignore si lap_button
    duration_value: Optional[float] = None

    target_type: TargetType = Field(default=TargetType.no_target)
    # Pour pace: m/s ; pour heart_rate: bpm
    target_low: Optional[float] = None
    target_high: Optional[float] = None

    notes: Optional[str] = None

    session: Optional[Session] = Relationship(back_populates="steps")
