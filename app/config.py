"""Application settings.

Env-driven with the same variable names the Spring version used, so an existing
ConfigMap / InfisicalSecret keeps working unchanged. The one exception is DB_URL:
SQLAlchemy needs a `postgresql+asyncpg://` URL instead of a JDBC one, so a JDBC
URL is translated on the fly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    profile: str = Field(default="prod", alias="APP_PROFILE")
    server_port: int = Field(default=8080, alias="SERVER_PORT")

    db_url: str = Field(
        default="postgresql+asyncpg://postgres:password@localhost:5432/ff_trainingskarte",
        alias="DB_URL",
    )
    db_user: str = Field(default="postgres", alias="DB_USER")
    db_password: str = Field(default="password", alias="DB_PASSWORD")

    redis_host: str = Field(default="localhost", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_password: str = Field(default="", alias="REDIS_HOST_PASSWORD")
    # Own database index so a FLUSHDB on another app's sessions cannot reach ours.
    redis_db: int = Field(default=0, alias="REDIS_DB")

    session_ttl_seconds: int = Field(default=60 * 60 * 8, alias="SESSION_TTL_SECONDS")
    session_cookie_secure: bool = Field(default=False, alias="SESSION_COOKIE_SECURE")

    nominatim_url: str = Field(default="https://nominatim.openstreetmap.org/search", alias="NOMINATIM_URL")
    nominatim_user_agent: str = Field(
        default="ffw-trainingskarte/1.0 (privates Hobby-Projekt)", alias="NOMINATIM_USER_AGENT"
    )

    @field_validator("db_url")
    @classmethod
    def normalize_db_url(cls, value: str) -> str:
        """Accept the JDBC URL the Spring deployment already sets."""
        if value.startswith("jdbc:postgresql://"):
            return "postgresql+asyncpg://" + value[len("jdbc:postgresql://") :]
        if value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value[len("postgresql://") :]
        return value

    @property
    def is_dev(self) -> bool:
        return self.profile == "dev"

    @property
    def sqlalchemy_url(self) -> str:
        """Inject DB_USER / DB_PASSWORD when the URL carries no credentials."""
        if "@" in self.db_url.split("//", 1)[-1]:
            return self.db_url
        scheme, rest = self.db_url.split("//", 1)
        return f"{scheme}//{self.db_user}:{self.db_password}@{rest}"

    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def read_version() -> str:
    """Read version.txt once at startup (mirrors VersionController.init())."""
    for candidate in (BASE_DIR / "version.txt", PROJECT_ROOT / "version.txt"):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip()
    return "unknown"
