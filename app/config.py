from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    secret_key: str = Field(min_length=32)
    access_token_minutes: int = Field(default=30, ge=1, le=1440)
    reservation_minutes: int = Field(default=10, ge=1, le=60)
    max_tickets_per_order: int = Field(default=6, ge=1, le=20)
    rate_limit_enabled: bool = True
    demo_payments_enabled: bool = True

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value):
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("Use a postgresql+psycopg:// database URL.")
        return value

    @field_validator("secret_key")
    @classmethod
    def real_secret(cls, value):
        if value.startswith("replace-with-"):
            raise ValueError("Generate your own SECRET_KEY; see README.md.")
        return value


@lru_cache
def get_settings():
    return Settings()
