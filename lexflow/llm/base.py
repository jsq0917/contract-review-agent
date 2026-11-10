"""Provider 抽象：主推理/结构化抽取都通过统一接口，mock 与 API 可替换。

``complete_json`` 保持原三元组返回，兼容已有调用方与测试桩；
需要调用治理信息（重试、降级、模型名）时用 ``complete_json_with_meta``。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from lexflow.llm.errors import (
    AuthError,
    BadJSONError,
    BadRequestError,
    ConnectionFailed,
    EmptyOutputError,
    NotImplementedProviderError,
    ProviderError,
    ProviderTimeout,
    RateLimitError,
    SchemaMismatchError,
    ServerError,
    classify_exception,
)

__all__ = [
    "AuthError",
    "BadJSONError",
    "BadRequestError",
    "CallMeta",
    "CompletionResult",
    "ConnectionFailed",
    "EmptyOutputError",
    "LLMProvider",
    "NotImplementedProviderError",
    "ProviderError",
    "ProviderTimeout",
    "RateLimitError",
    "SchemaMismatchError",
    "ServerError",
    "classify_exception",
]


@dataclass
class CallMeta:
    """单次逻辑调用的治理信息；与 token 计数一起进 trace。"""

    provider: str
    model: str
    prompt_version: str = ""
    attempts: int = 1
    retries: int = 0
    fallback_from: str | None = None
    degraded_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_estimate_cny: float | None = None
    latency_ms: int = 0
    status: str = "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "attempts": self.attempts,
            "retries": self.retries,
            "fallback_from": self.fallback_from,
            "degraded_reason": self.degraded_reason,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_estimate_cny": self.cost_estimate_cny,
            "latency_ms": self.latency_ms,
            "status": self.status,
        }


@dataclass
class CompletionResult:
    payload: dict[str, Any]
    prompt_tokens: int
    completion_tokens: int
    meta: CallMeta


@dataclass
class ToolCallRequest:
    """模型请求调用一个工具。"""

    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolCallResponse:
    """一轮 Function Calling 的结果。"""

    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    payload: dict[str, Any] | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    meta: CallMeta | None = None
    raw_content: str = ""


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

    def complete_json_with_meta(
        self,
        system: str,
        user: str,
        schema_hint: dict[str, Any] | None = None,
        *,
        validator: Callable[[Any], Any] | None = None,
        prompt_version: str = "",
    ) -> CompletionResult:
        """默认实现：包一层 ``complete_json``，治理信息记为一次成功调用。"""
        import time

        started = time.perf_counter()
        payload, prompt_tokens, completion_tokens = self.complete_json(system, user, schema_hint)
        if validator is not None:
            try:
                validator(payload)
            except SchemaMismatchError:
                raise
            except Exception as exc:  # noqa: BLE001 - 统一转成可分类错误
                raise SchemaMismatchError(f"模型输出不符合 Schema: {exc}") from exc
        return CompletionResult(
            payload=payload,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            meta=CallMeta(
                provider=self.engine,
                model=self.model,
                prompt_version=prompt_version,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=int((time.perf_counter() - started) * 1000),
            ),
        )

    def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        tool_choice: str = "auto",
        prompt_version: str = "",
    ) -> ToolCallResponse:
        """Function Calling 接口。默认不支持，由支持的 Provider 覆盖。"""
        raise NotImplementedProviderError(f"{type(self).__name__} 不支持 Function Calling")
