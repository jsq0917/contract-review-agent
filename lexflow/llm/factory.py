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
        return OpenAICompatibleProvider(
            settings.api_key,
            settings.base_url,
            settings.model,
            fallback_models=settings.fallback_models,
            fallback_api_key=settings.fallback_api_key,
            connect_timeout_s=settings.llm_connect_timeout_s,
            read_timeout_s=settings.llm_read_timeout_s,
            total_deadline_s=settings.llm_total_deadline_s,
            max_retries=settings.llm_max_retries,
            backoff_base_s=settings.llm_backoff_base_s,
            max_concurrency=settings.llm_max_concurrency,
            price_input_per_m=settings.price_input_per_m,
            price_output_per_m=settings.price_output_per_m,
        )
    if engine == "auto":
        if settings.api_key:
            return build_provider(settings, "api")
        return MockProvider()
    raise ValueError(f"未知 engine: {engine}，可选 mock/api/auto")
