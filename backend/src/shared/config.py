"""Application settings loaded from environment variables / `.env`."""

from functools import lru_cache
from typing import Annotated, Literal

from fastapi import Depends, Request
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Settings model
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Look in the backend dir first, then the repo root, so one root `.env` works.
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Travel Planner API"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # Persistence
    database_url: str = "sqlite+aiosqlite:///./travel_planner.db"

    # Auth
    jwt_secret: str = Field(default="change-me-in-dev-only", min_length=16)
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24
    # Cost factor for password hashing; tests drop this to the minimum (4) for speed.
    bcrypt_rounds: int = Field(default=12, ge=4, le=16)

    # Local LLM (Ollama)
    llm_provider: Literal["ollama", "fake"] = "ollama"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    # Reasoning ("thinking") mode for models that support it. Off by default: it adds
    # long pauses before the first token on a small local model.
    llm_think: bool = False
    llm_timeout_seconds: float = Field(default=120.0, gt=0)
    llm_max_attempts: int = Field(default=3, ge=1, le=10)

    # Web search: "tavily" needs TAVILY_API_KEY; "fake" returns placeholder results offline.
    search_provider: Literal["tavily", "fake"] = "tavily"
    tavily_api_key: str | None = None
    search_timeout_seconds: float = Field(default=15.0, gt=0)
    search_cache_ttl_hours: float = Field(default=24.0, ge=0)

    # HTTP
    cors_origins: list[str] = ["http://localhost:5173"]


# ---------------------------------------------------------------------------
# Accessor
# ---------------------------------------------------------------------------


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (cached). Used only when building the app."""
    return Settings()


def get_app_settings(request: Request) -> Settings:
    """FastAPI dependency: the settings the running app was created with (see `create_app`)."""
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
