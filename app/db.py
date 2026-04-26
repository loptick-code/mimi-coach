"""Engine SQLModel + helpers de session DB.

Supporte deux backends :
- SQLite (dev local) : fichier dans data/app.db
- Postgres (prod, Vercel + Supabase/Neon) : connection string postgres://...
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine

from app.config import get_settings


def _normalize_db_url(url: str) -> str:
    """Convertit les anciens schemas postgres:// vers postgresql+psycopg://."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        # Force le driver psycopg v3 (sinon SQLAlchemy tente psycopg2 par defaut)
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _build_engine():
    settings = get_settings()
    url = _normalize_db_url(settings.database_url)

    is_sqlite = url.startswith("sqlite")
    is_postgres = url.startswith("postgresql")

    # Cree le repertoire pour SQLite local
    if is_sqlite:
        # sqlite:///./data/app.db ou sqlite:////absolute/path
        path_part = url.split(":///", 1)[1] if ":///" in url else ""
        if path_part.startswith("./"):
            path_part = path_part[2:]
        if path_part:
            Path(path_part).parent.mkdir(parents=True, exist_ok=True)

    connect_args: dict = {}
    if is_sqlite:
        connect_args["check_same_thread"] = False

    engine_kwargs: dict = {"echo": False, "connect_args": connect_args}
    if is_postgres:
        # Pool config raisonnable pour serverless (Vercel)
        engine_kwargs["pool_pre_ping"] = True
        engine_kwargs["pool_recycle"] = 300
        engine_kwargs["pool_size"] = 5
        engine_kwargs["max_overflow"] = 5

    return create_engine(url, **engine_kwargs)


engine = _build_engine()


def init_db() -> None:
    """Cree les tables si elles n'existent pas."""
    from app import models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[DBSession]:
    """Dependency FastAPI : ouvre une session DB par requete."""
    with DBSession(engine) as session:
        yield session
