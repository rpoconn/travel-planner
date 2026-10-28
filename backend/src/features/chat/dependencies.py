"""LLM client construction and the `LLMDep` dependency."""

from typing import Annotated

from fastapi import Depends, Request

from features.chat.offline import offline_reply
from shared.config import Settings
from shared.llm.fake import FakeLLM
from shared.llm.ollama_client import OllamaClient
from shared.llm.types import LLMClient


def create_llm(settings: Settings) -> LLMClient:
    """Build the client for `settings.llm_provider`; `create_app` stores it on app.state."""
    if settings.llm_provider == "fake":
        return FakeLLM(fallback=offline_reply)
    return OllamaClient(
        host=settings.ollama_url,
        model=settings.ollama_model,
        think=settings.llm_think,
        timeout_seconds=settings.llm_timeout_seconds,
        max_attempts=settings.llm_max_attempts,
    )


def get_llm(request: Request) -> LLMClient:
    """The app's LLM client (tests swap `app.state.llm` for a scripted `FakeLLM`)."""
    llm: LLMClient = request.app.state.llm
    return llm


LLMDep = Annotated[LLMClient, Depends(get_llm)]
