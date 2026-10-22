import pytest
from app.core.config import Settings
from pydantic import ValidationError

GOOD_KEY = "Ab9!xyz_" * 4
DATABASE_URL = "postgresql+asyncpg://example"


def test_production_rejects_weak_and_placeholder_secrets():
    for key in ("weak", "change-me-" * 4):
        with pytest.raises(ValidationError, match="SECRET_KEY must be at least 32 characters"):
            Settings(
                _env_file=None,
                secret_key=key,
                database_url=DATABASE_URL,
                environment="prod",
            )


def test_settings_default_to_safe_production(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT")
    config = Settings(_env_file=None, secret_key=GOOD_KEY, database_url=DATABASE_URL)
    assert config.environment == "prod"
    assert config.log_json is True


def test_settings_reject_unknown_environment():
    with pytest.raises(ValidationError, match="environment"):
        Settings(
            _env_file=None,
            secret_key=GOOD_KEY,
            database_url=DATABASE_URL,
            environment="stage",
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("login_rate_limit_attempts", 0),
        ("login_rate_limit_window_seconds", 0),
        ("login_rate_limit_ip_multiplier", 0),
    ],
)
def test_rate_limit_settings_must_be_positive(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, secret_key=GOOD_KEY, database_url=DATABASE_URL, **{field: value})
