from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    secret_key: str
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30
    invitation_expire_days: int = 7
    database_url: str
    environment: Literal["dev", "test", "prod"] = "prod"
    redis_url: str = "redis://redis:6379/0"
    log_level: str = "INFO"
    log_json: bool = False
    sql_echo: bool = False
    login_rate_limit_attempts: int = Field(default=5, ge=1)
    login_rate_limit_window_seconds: int = Field(default=300, ge=1)
    login_rate_limit_ip_multiplier: int = Field(default=5, ge=1)
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    smtp_timeout_seconds: float = Field(default=10, gt=0)
    mail_from: str = "noreply@localhost"
    invitation_accept_url: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("secret_key")
    @classmethod
    def validate_secret_key(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("SECRET_KEY must not be empty")
        return value

    @field_validator("smtp_host", "smtp_username", "smtp_password", "invitation_accept_url")
    @classmethod
    def blank_to_none(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return value.strip()

    @field_validator("invitation_accept_url")
    @classmethod
    def validate_invitation_accept_url(cls, value: str | None) -> str | None:
        if value is not None and "{token}" not in value:
            raise ValueError("INVITATION_ACCEPT_URL must contain the {token} placeholder")
        return value

    @model_validator(mode="after")
    def validate_production_secret(self):
        placeholders = {
            "",
            "changeme",
            "change-me",
            "secret",
            "your-secret-key",
            "openssl rand -hex 32",
        }
        lowered_key = self.secret_key.lower()
        has_placeholder_text = any(
            phrase in lowered_key
            for phrase in (
                "change-me",
                "changeme",
                "your-secret",
                "your_secret",
                "replace-me",
                "replace_me",
                "openssl rand",
            )
        )
        if self.environment == "prod" and (
            len(self.secret_key) < 32
            or len(set(self.secret_key)) == 1
            or lowered_key in placeholders
            or has_placeholder_text
        ):
            raise ValueError(
                "SECRET_KEY must be at least 32 characters and not a placeholder in prod"
            )
        if self.environment == "prod":
            self.log_json = True
        return self


settings = Settings()

ALGORITHM = "HS256"
