"""LLM client construction, the `LLMDep` dependency, and the chat rate limit."""

import logging
from typing import Annotated

from fastapi import Depends, Request

from features.planning.agent.offline import agent_reply
from shared.config import Settings
from shared.llm.anthropic_client import AnthropicClient
from shared.llm.fake import FakeLLM
from shared.llm.ollama_client import OllamaClient
from shared.llm.types import LLMClient
from shared.rate_limit import CHAT_BUCKET, enforce_rate_limit
from shared.security import CurrentUserId

logger = logging.getLogger(__name__)


def create_llm(settings: Settings) -> LLMClient:
    """Build the client for `settings.llm_provider`; `create_app` stores it on app.state."""
    if settings.llm_provider == "fake":
        return FakeLLM(fallback=agent_reply)
    if settings.llm_provider == "anthropic":
        api_key = settings.anthropic_api_key
        if api_key is None or not api_key.get_secret_value():
            logger.warning("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
        return AnthropicClient(
            api_key=api_key.get_secret_value() if api_key else None,
            model=settings.anthropic_model,
            think=settings.llm_think,
            max_tokens=settings.anthropic_max_tokens,
            timeout_seconds=settings.llm_timeout_seconds,
            max_attempts=settings.llm_max_attempts,
            refusal_fallback=settings.anthropic_refusal_fallback,
        )
    return OllamaClient(
        host=settings.ollama_url,
        model=settings.ollama_model,
        think=settings.llm_think,
        timeout_seconds=settings.llm_timeout_seconds,
        context_tokens=settings.llm_context_tokens,
        max_output_tokens=settings.llm_max_output_tokens,
        max_attempts=settings.llm_max_attempts,
    )


def get_llm(request: Request) -> LLMClient:
    """The app's LLM client (tests swap `app.state.llm` for a scripted `FakeLLM`)."""
    llm: LLMClient = request.app.state.llm
    return llm


LLMDep = Annotated[LLMClient, Depends(get_llm)]


def limit_chat_messages(request: Request, user_id: CurrentUserId) -> None:
    """Per-user cap on planning turns that may run the model or a metered search."""
    enforce_rate_limit(request, CHAT_BUCKET, str(user_id), "You're sending messages too quickly.")
