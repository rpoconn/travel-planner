"""Application settings loaded from environment variables / `.env`."""

from functools import lru_cache
from typing import Literal

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

    # Local LLM (Ollama)
    llm_provider: Literal["ollama", "fake"] = "ollama"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"

    # Web search
    tavily_api_key: str | None = None

    # HTTP
    cors_origins: list[str] = ["http://localhost:5173"]


# ---------------------------------------------------------------------------
# Accessor
# ---------------------------------------------------------------------------


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (cached; override via FastAPI dependency_overrides)."""
    return Settings()
