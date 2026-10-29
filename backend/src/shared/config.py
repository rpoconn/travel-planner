"""Application settings loaded from environment variables / `.env`."""

from functools import lru_cache
from typing import Annotated, Literal, Self

from fastapi import Depends, Request
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Settings model
# ---------------------------------------------------------------------------

# 32+ bytes so PyJWT doesn't warn about a short HMAC key; refused in production anyway.
_DEV_JWT_SECRET = "change-me-in-dev-only-not-a-real-secret"


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
    jwt_secret: str = Field(default=_DEV_JWT_SECRET, min_length=16)
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
    # Ollama's default 4096-token window is too small once the plan JSON, tool schemas and
    # web search results are in the prompt. When it overflows, llama.cpp drops the oldest
    # tokens (the system prompt and tools) and the model rambles until the read times out.
    llm_context_tokens: int = Field(default=16384, ge=2048)
    # Cap on tokens per reply, so a runaway generation ends instead of stalling the stream.
    llm_max_output_tokens: int = Field(default=2048, ge=128)

    # Web search: "tavily" needs TAVILY_API_KEY; "fake" returns placeholder results offline.
    search_provider: Literal["tavily", "fake"] = "tavily"
    tavily_api_key: str | None = None
    search_timeout_seconds: float = Field(default=15.0, gt=0)
    search_cache_ttl_hours: float = Field(default=24.0, ge=0)

    # HTTP
    cors_origins: list[str] = ["http://localhost:5173"]
    # Largest accepted request body; the biggest legitimate one (an item or plan with 5000
    # chars of notes) is a few KB.
    max_request_bytes: int = Field(default=64 * 1024, ge=1024)
    # Per-minute limits (0 disables). Chat is per user: each message runs the local model,
    # so this mostly protects the machine. Auth (login + register) is per client IP.
    chat_rate_limit_per_minute: int = Field(default=20, ge=0)
    auth_rate_limit_per_minute: int = Field(default=10, ge=0)

    @model_validator(mode="after")
    def _check_production(self) -> Self:
        # Dev defaults that would be unsafe if the app were ever exposed.
        if self.environment == "production":
            if self.jwt_secret == _DEV_JWT_SECRET or len(self.jwt_secret) < 32:
                raise ValueError("Set JWT_SECRET to a random value of 32+ characters")
            if "*" in self.cors_origins:
                raise ValueError("CORS_ORIGINS must list explicit origins in production")
        return self


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
