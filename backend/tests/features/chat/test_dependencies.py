"""`create_llm` builds the client LLM_PROVIDER selects."""

import pytest
from pydantic import SecretStr

from features.chat.dependencies import create_llm
from shared.config import Settings
from shared.llm.anthropic_client import AnthropicClient
from shared.llm.fake import FakeLLM
from shared.llm.ollama_client import OllamaClient


def _settings(**overrides: str | SecretStr | None) -> Settings:
    # `_env_file=None`: the developer's `.env` must not decide what these tests see.
    return Settings.model_validate({"_env_file": None, **overrides})


@pytest.mark.parametrize(
    ("provider", "client_type", "model"),
    [
        ("anthropic", AnthropicClient, "claude-haiku-5-5"),
        ("ollama", OllamaClient, "qwen3:8b"),
        ("fake", FakeLLM, "fake"),
    ],
)
async def test_provider_selects_client_and_model(
    provider: str, client_type: type, model: str
) -> None:
    settings = _settings(llm_provider=provider, anthropic_api_key=SecretStr("sk-test"))

    llm = create_llm(settings)

    assert isinstance(llm, client_type)
    assert settings.llm_model == model
    await llm.aclose()


def test_anthropic_model_comes_from_settings() -> None:
    settings = _settings(llm_provider="anthropic", anthropic_model="claude-sonnet-5-5")

    assert settings.llm_model == "claude-sonnet-5-5"


def test_missing_anthropic_key_warns_but_still_builds(caplog: pytest.LogCaptureFixture) -> None:
    llm = create_llm(_settings(llm_provider="anthropic", anthropic_api_key=None))

    assert isinstance(llm, AnthropicClient)
    assert "ANTHROPIC_API_KEY is not set" in caplog.text
