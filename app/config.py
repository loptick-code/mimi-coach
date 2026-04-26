"""Configuration centralisee via pydantic-settings."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Reglages applicatifs charges depuis l'environnement / .env."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    fernet_key: str = Field(default="")
    session_secret: str = Field(default="dev-secret-change-me-in-production")

    database_url: str = Field(default="sqlite:///./data/app.db")

    coach_email: str = Field(default="coach@example.com")
    coach_password: str = Field(default="changeme")
    athlete_email: str = Field(default="athlete@example.com")
    athlete_password: str = Field(default="changeme")
    athlete_name: str = Field(default="Athlete")

    debug: bool = Field(default=True)
    # En production : True pour cookies https-only (Railway/Render servent en https)
    https_only_cookies: bool = Field(default=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
