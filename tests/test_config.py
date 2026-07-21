import pytest
from pydantic import ValidationError

from crm.config import Settings


@pytest.mark.parametrize("scheme", ["postgres://", "postgresql://"])
def test_railway_database_url_uses_asyncpg(scheme: str) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"{scheme}user:password@host:5432/database",
    )

    assert settings.database_url == "postgresql+asyncpg://user:password@host:5432/database"


def test_production_rejects_default_secret() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="production",
            app_secret_key="local-development-secret-change-me",
        )


@pytest.mark.parametrize(
    ("raw_origins", "expected"),
    [
        (
            '["https://crm.example","http://localhost:5173"]',
            ["https://crm.example", "http://localhost:5173"],
        ),
        (
            "https://crm.example,http://localhost:5173",
            ["https://crm.example", "http://localhost:5173"],
        ),
    ],
)
def test_cors_origins_accept_json_and_comma_separated_values(
    raw_origins: str, expected: list[str]
) -> None:
    settings = Settings(_env_file=None, cors_origins=raw_origins)

    assert settings.cors_origins == expected
