"""Provider 工厂：显式 mock / 显式 api / auto 三种模式。"""

from __future__ import annotations

from lexflow.config import Settings
from lexflow.llm.base import LLMProvider
from lexflow.llm.mock import MockProvider
from lexflow.llm.openai_compat import OpenAICompatibleProvider


def build_provider(settings: Settings, engine: str = "auto") -> LLMProvider:
    engine = (engine or "auto").lower()
    if engine == "mock":
        return MockProvider()
    if engine == "api":
        return OpenAICompatibleProvider(settings.api_key, settings.base_url, settings.model)
    if engine == "auto":
        if settings.api_key:
            return OpenAICompatibleProvider(settings.api_key, settings.base_url, settings.model)
        return MockProvider()
    raise ValueError(f"未知 engine: {engine}，可选 mock/api/auto")
