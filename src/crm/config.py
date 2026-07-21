import json
from functools import lru_cache
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Content Production CRM"
    app_env: str = "local"
    app_secret_key: str = "local-development-secret-change-me"
    database_url: str = "postgresql+asyncpg://crm:crm@localhost:5432/crm"
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    access_token_expire_minutes: int = 480

    @field_validator("database_url", mode="before")
    @classmethod
    def use_async_postgres_driver(cls, value):
        """Railway exposes a regular Postgres URL; SQLAlchemy uses asyncpg here."""
        if not isinstance(value, str):
            return value
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value):
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                return json.loads(stripped)
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def require_production_secret(self):
        if (
            self.app_env.lower() == "production"
            and self.app_secret_key == "local-development-secret-change-me"
        ):
            raise ValueError("APP_SECRET_KEY must be set to a secure value in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
