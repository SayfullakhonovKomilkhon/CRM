import json
from functools import lru_cache
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class GoogleSheetsTabConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tab: str = Field(min_length=1, max_length=255)
    header_row: int = Field(default=1, ge=1, le=10_000)
    project_id: UUID
    assigned_scenarist_id: UUID | None = None
    columns: dict[str, int | str] = Field(default_factory=dict)

    @field_validator("columns")
    @classmethod
    def validate_column_references(cls, value):
        for field_name, reference in value.items():
            if not field_name.strip():
                raise ValueError("Google Sheets column field cannot be blank")
            if isinstance(reference, int) and reference < 1:
                raise ValueError("Google Sheets column indexes are 1-based")
            if isinstance(reference, str) and not reference.strip():
                raise ValueError("Google Sheets column header cannot be blank")
        return value


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
    google_sheets_enabled: bool = False
    google_service_account_json: SecretStr | None = None
    google_sheets_spreadsheet_id: str | None = None
    google_sheets_tab_configs: Annotated[list[GoogleSheetsTabConfig], NoDecode] = Field(
        default_factory=list
    )
    google_sheets_preview_ttl_minutes: int = 30
    google_sheets_max_rows: int = 1_000
    redis_url: str | None = None
    sheet_webhook_max_age_seconds: int = 300
    sheet_google_max_retries: int = 4

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

    @field_validator("google_sheets_tab_configs", mode="before")
    @classmethod
    def parse_google_sheets_tab_configs(cls, value):
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            return json.loads(stripped)
        return value

    @field_validator("google_sheets_spreadsheet_id", mode="before")
    @classmethod
    def normalize_google_sheets_spreadsheet_id(cls, value):
        if not isinstance(value, str):
            return value
        return value.strip() or None

    @field_validator("google_service_account_json", mode="before")
    @classmethod
    def normalize_google_service_account_json(cls, value):
        if not isinstance(value, str):
            return value
        return value.strip() or None

    @model_validator(mode="after")
    def require_production_secret(self):
        if (
            self.app_env.lower() == "production"
            and self.app_secret_key == "local-development-secret-change-me"
        ):
            raise ValueError("APP_SECRET_KEY must be set to a secure value in production")
        if self.google_sheets_preview_ttl_minutes < 1:
            raise ValueError("GOOGLE_SHEETS_PREVIEW_TTL_MINUTES must be positive")
        if not 1 <= self.google_sheets_max_rows <= 10_000:
            raise ValueError("GOOGLE_SHEETS_MAX_ROWS must be between 1 and 10000")
        if not 30 <= self.sheet_webhook_max_age_seconds <= 3600:
            raise ValueError("SHEET_WEBHOOK_MAX_AGE_SECONDS must be between 30 and 3600")
        if not 1 <= self.sheet_google_max_retries <= 10:
            raise ValueError("SHEET_GOOGLE_MAX_RETRIES must be between 1 and 10")
        tabs = [item.tab.casefold() for item in self.google_sheets_tab_configs]
        if len(tabs) != len(set(tabs)):
            raise ValueError("GOOGLE_SHEETS_TAB_CONFIGS contains duplicate tab names")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
