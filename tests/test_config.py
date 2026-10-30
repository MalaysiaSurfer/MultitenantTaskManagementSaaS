import json
import logging

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


def test_production_app_disables_docs_and_uses_json_logging():
    from app.main import RequestFormatter, configure_logging, create_app

    config = Settings(
        _env_file=None,
        secret_key=GOOD_KEY,
        database_url=DATABASE_URL,
        environment="prod",
        log_json=False,
    )
    assert create_app(config).docs_url is None
    assert create_app(config).redoc_url is None
    assert create_app(config).openapi_url is None

    root = logging.getLogger()
    old_handlers, old_level = root.handlers[:], root.level
    try:
        configure_logging(config)
        assert isinstance(root.handlers[0].formatter, RequestFormatter)
        record = logging.LogRecord("production", logging.INFO, "", 0, "ready", (), None)
        record.request_id = "req-1"
        record.user_id = "-"
        record.organization_id = "-"
        assert json.loads(root.handlers[0].formatter.format(record))["message"] == "ready"
    finally:
        root.handlers = old_handlers
        root.setLevel(old_level)


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


def test_invitation_accept_url_requires_token_placeholder():
    with pytest.raises(ValidationError, match="INVITATION_ACCEPT_URL"):
        Settings(
            _env_file=None,
            secret_key=GOOD_KEY,
            database_url=DATABASE_URL,
            invitation_accept_url="https://app.example.com/invite",
        )


def test_blank_smtp_host_disables_mail_delivery():
    config = Settings(
        _env_file=None, secret_key=GOOD_KEY, database_url=DATABASE_URL, smtp_host="  "
    )
    assert config.smtp_host is None
