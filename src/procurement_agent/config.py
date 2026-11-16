"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated runtime configuration.

    Secrets never leave typed SecretStr fields; callers must call `.get_secret_value()`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["local", "test", "prod"] = "local"
    app_log_level: str = "INFO"
    app_timezone: str = "Europe/Berlin"

    erpnext_base_url: str = "http://localhost:8080"
    erpnext_site: str = "frontend"

    llm_provider: Literal["openai", "deepseek", "anthropic", "gemini"] = "openai"
    llm_model: str = "gpt-4.1-mini"
    openai_api_key: SecretStr | None = None

    embedding_model: str = "BAAI/bge-small-en-v1.5"

    slack_bot_token: SecretStr | None = None
    slack_app_token: SecretStr | None = None
    slack_signing_secret: SecretStr | None = None
    slack_channel_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
