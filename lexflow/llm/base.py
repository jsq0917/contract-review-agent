"""Provider 抽象：主推理/结构化抽取都通过统一接口，mock 与 API 可替换。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class EmptyOutputError(ValueError):
    """Provider 返回空内容；与业务上的 findings=[] 区分。"""

    category = "empty_output"


class LLMProvider(ABC):
    engine: str
    model: str

    @abstractmethod
    def complete_json(
        self,
        system: str,
        user: str,
        schema_hint: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], int, int]:
        """返回 (JSON dict, prompt_tokens, completion_tokens)。"""
