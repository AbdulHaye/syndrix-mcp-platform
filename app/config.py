from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_env: str
    secret_key: str
    api_host: str
    api_port: int

    # Database
    database_url: str

    # Redis
    redis_url: str

    # Ollama
    ollama_base_url: str
    ollama_default_model: str
    ollama_embed_model: str

    # Raw dev tokens string from env (format: team_name:token,team_name:token)
    dev_tokens: str

    # Adapter credentials — all optional
    podio_client_id: str | None = None
    podio_client_secret: str | None = None
    podio_app_id: str | None = None

    ghl_api_key: str | None = None
    ghl_location_id: str | None = None

    slack_bot_token: str | None = None
    slack_signing_secret: str | None = None

    github_token: str | None = None
    github_org: str | None = None

    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None

    @field_validator("smtp_port", mode="before")
    @classmethod
    def _coerce_smtp_port(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    # Parsed token map — token -> team_name
    _token_map: dict[str, str] = {}

    @model_validator(mode="after")
    def parse_dev_tokens(self) -> "Settings":
        token_map: dict[str, str] = {}
        raw = self.dev_tokens.strip()
        if raw:
            for pair in raw.split(","):
                pair = pair.strip()
                if ":" in pair:
                    parts = pair.split(":", 1)
                    team_name = parts[0].strip()
                    token = parts[1].strip()
                    if team_name and token:
                        token_map[token] = team_name
        self._token_map = token_map
        return self

    def get_token_map(self) -> dict[str, str]:
        return self._token_map

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
