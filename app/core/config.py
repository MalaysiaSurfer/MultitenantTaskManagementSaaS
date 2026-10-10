from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    secret_key: str
    access_token_expire_minutes: int
    refresh_token_expire_days: int
    database_url: str

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()

ALGORITHM = "HS256"
