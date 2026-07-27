from __future__ import annotations

from functools import lru_cache

from dotenv import load_dotenv
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env into os.environ early so plain os.environ.get() lookups
# (e.g. PGVECTOR_ENABLED in storage modules) see the configured values.
load_dotenv()


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

    # JWT
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    # Bootstrap admin (used once on first startup if no admin user exists)
    admin_email: str = "admin@syndrix.local"
    admin_password: str = "changeme"

    # Raw dev tokens string from env (format: team_name:token,team_name:token)
    dev_tokens: str = ""

    # Comma-separated list of allowed CORS origins (e.g. deployed frontend URLs).
    # localhost:3000 is always allowed for local dev regardless of this setting.
    cors_origins: str = ""

    # Parsed token map — token -> team_name
    _token_map: dict[str, str] = {}

    @model_validator(mode="after")
    def parse_dev_tokens(self) -> "Settings":
        # Use secret_key as JWT secret if jwt_secret_key not explicitly set
        if not self.jwt_secret_key:
            self.jwt_secret_key = self.secret_key

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

    def get_cors_origins(self) -> list[str]:
        origins = {"http://localhost:3000"}
        for origin in self.cors_origins.split(","):
            origin = origin.strip().rstrip("/")
            if origin:
                origins.add(origin)
        return sorted(origins)

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
