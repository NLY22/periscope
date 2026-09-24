"""Tests for the Agnes provider wiring (free OpenAI-compatible tier).

The API key itself is never present here — what these tests pin is the
contract: default model/base_url/env-var name, key resolution errors,
and that chained configs expand agnes correctly.
"""

from __future__ import annotations

import pytest

from src.ai.client import OpenAIClient, create_ai_client
from src.models import AI_PROVIDER_DEFAULTS, AIConfig, AIProvider


def _agnes_config(**overrides) -> AIConfig:
    defaults = AI_PROVIDER_DEFAULTS[AIProvider.AGNES]
    data = {
        "provider": AIProvider.AGNES,
        "model": defaults["model"],
        "api_key_env": defaults["api_key_env"],
        "temperature": 0.3,
        "max_tokens": 4096,
    }
    data.update(overrides)
    return AIConfig(**data)


def test_agnes_defaults_point_at_apihub() -> None:
    defaults = AI_PROVIDER_DEFAULTS[AIProvider.AGNES]
    assert defaults["model"] == "agnes-2.5-flash"
    assert defaults["api_key_env"] == "AGNES_API_KEY"
    assert defaults["base_url"] == "https://apihub.agnes-ai.com/v1"


def test_agnes_uses_openai_compatible_client(monkeypatch) -> None:
    monkeypatch.setenv("AGNES_API_KEY", "test-key")
    client = create_ai_client(_agnes_config())
    assert isinstance(client, OpenAIClient)
    assert str(client.client.base_url).rstrip("/") == "https://apihub.agnes-ai.com/v1"
    assert client.model == "agnes-2.5-flash"


def test_agnes_missing_key_raises_valueerror(monkeypatch) -> None:
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    with pytest.raises(ValueError, match="AGNES_API_KEY"):
        create_ai_client(_agnes_config())


def test_agnes_config_base_url_overrides_default(monkeypatch) -> None:
    monkeypatch.setenv("AGNES_API_KEY", "test-key")
    client = create_ai_client(_agnes_config(base_url="https://mirror.example/v1"))
    assert str(client.client.base_url).rstrip("/") == "https://mirror.example/v1"


def test_agnes_in_provider_chain(monkeypatch) -> None:
    from src.ai.client import ChainedAIClient

    monkeypatch.setenv("AGNES_API_KEY", "test-key")
    cfg = AIConfig(
        provider=AIProvider.OPENAI,
        model="gpt-4",
        api_key_env="OPENAI_API_KEY",
        provider_chain="openai,agnes",
    )
    chain = create_ai_client(cfg)
    assert isinstance(chain, ChainedAIClient)
    agnes_cfg = chain.configs[1]
    assert agnes_cfg.provider == AIProvider.AGNES
    assert agnes_cfg.base_url == "https://apihub.agnes-ai.com/v1"
    assert agnes_cfg.api_key_env == "AGNES_API_KEY"
