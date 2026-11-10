"""OpenAI 兼容 API Provider（DeepSeek/Qwen 等）。

治理点：
- 显式 connect/read 超时 + 整次逻辑调用的总 Deadline；
- 只对 ``retriable`` 错误做有限指数退避；401/400 不重试；
- 坏 JSON、Schema 不符分开抛（``bad_json`` / ``schema_mismatch``）；
- 并发信号量：同一 Provider 实例被法务/商务两路共享，避免同时打满上游；
- 备用模型（可配置）与备用 API Key（独立配置）分开处理；
- 每次调用返回 ``CallMeta``，由调用方写入 trace；日志不落 Key 与正文。

没有 API key 时构造会抛错；调用方应先走 factory 的 mock 降级。
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

import httpx

from lexflow.llm.base import (
    CallMeta,
    CompletionResult,
    LLMProvider,
    ToolCallRequest,
    ToolCallResponse,
)
from lexflow.llm.errors import (
    AuthError,
    BadJSONError,
    EmptyOutputError,
    ProviderError,
    ProviderTimeout,
    SchemaMismatchError,
    classify_exception,
)
from lexflow.logging import get_logger, log_event

logger = get_logger("lexflow.llm")


def _strip_fence(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


class OpenAICompatibleProvider(LLMProvider):
    engine = "api"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        fallback_models: tuple[str, ...] | list[str] = (),
        fallback_api_key: str = "",
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 120.0,
        total_deadline_s: float = 180.0,
        max_retries: int = 2,
        backoff_base_s: float = 0.5,
        max_concurrency: int = 4,
        price_input_per_m: str = "",
        price_output_per_m: str = "",
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("缺少 LEXFLOW_API_KEY，无法使用 api engine")
        from openai import OpenAI

        self.model = model
        self._models = [model, *[item for item in fallback_models if item and item != model]]
        self._fallback_api_key = fallback_api_key
        self._base_url = base_url
        self._connect_timeout_s = connect_timeout_s
        self._read_timeout_s = read_timeout_s
        self._total_deadline_s = total_deadline_s
        self._max_retries = max(0, int(max_retries))
        self._backoff_base_s = max(0.0, backoff_base_s)
        self._semaphore = threading.BoundedSemaphore(max(1, int(max_concurrency)))
        self._sleep = sleep
        self._price_input = price_input_per_m
        self._price_output = price_output_per_m
        self._timeout = httpx.Timeout(
            connect=connect_timeout_s,
            read=read_timeout_s,
            write=min(30.0, read_timeout_s),
            pool=read_timeout_s,
        )
        # 测试可注入假 client；注入后所有模型共用它。
        self._injected_client = client
        self._clients: dict[str, Any] = {}
        if client is not None:
            for name in self._models:
                self._clients[name] = client
        else:
            self._clients[model] = OpenAI(api_key=api_key, base_url=base_url, timeout=self._timeout)
            if self._models[1:]:
                fallback_key = fallback_api_key or api_key
                fallback_client = OpenAI(api_key=fallback_key, base_url=base_url, timeout=self._timeout)
                for name in self._models[1:]:
                    self._clients[name] = fallback_client

    # ---------- 对外接口 ----------

    def complete_json(
        self,
        system: str,
        user: str,
        schema_hint: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], int, int]:
        result = self._complete(system, user, schema_hint)
        return result.payload, result.prompt_tokens, result.completion_tokens

    def complete_json_with_meta(
        self,
        system: str,
        user: str,
        schema_hint: dict[str, Any] | None = None,
        *,
        validator: Callable[[Any], Any] | None = None,
        prompt_version: str = "",
    ) -> CompletionResult:
        return self._complete(system, user, schema_hint, validator=validator, prompt_version=prompt_version)

    def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        tool_choice: str = "auto",
        prompt_version: str = "",
    ) -> ToolCallResponse:
        started = time.perf_counter()

        def request(client: Any, model: str) -> Any:
            return client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=0,
                timeout=self._timeout,
            )

        resp, model, attempts, fallback_from, degraded = self._request_with_policy(request)
        message = resp.choices[0].message
        calls: list[ToolCallRequest] = []
        for item in getattr(message, "tool_calls", None) or []:
            raw_args = getattr(item.function, "arguments", "") or "{}"
            try:
                arguments = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                raise BadJSONError(f"工具参数不是合法 JSON: {raw_args[:200]}") from exc
            calls.append(ToolCallRequest(call_id=getattr(item, "id", ""), name=item.function.name, arguments=arguments))
        payload = None
        if not calls:
            content = getattr(message, "content", None)
            if content is None or not content.strip():
                raise EmptyOutputError("模型返回空内容")
            payload = self._parse_json(content)
        usage = getattr(resp, "usage", None)
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        meta = CallMeta(
            provider=self.engine,
            model=model,
            prompt_version=prompt_version,
            attempts=attempts,
            retries=max(0, attempts - 1),
            fallback_from=fallback_from,
            degraded_reason=degraded,
            prompt_tokens=pt,
            completion_tokens=ct,
            cost_estimate_cny=self._cost(model, pt, ct),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return ToolCallResponse(
            tool_calls=calls,
            payload=payload,
            prompt_tokens=pt,
            completion_tokens=ct,
            meta=meta,
            raw_content=getattr(message, "content", "") or "",
        )

    # ---------- 内部实现 ----------

    def _complete(
        self,
        system: str,
        user: str,
        schema_hint: dict[str, Any] | None,
        *,
        validator: Callable[[Any], Any] | None = None,
        prompt_version: str = "",
    ) -> CompletionResult:
        started = time.perf_counter()
        schema_text = ""
        if schema_hint:
            schema_text = "\n请严格输出 JSON，且符合以下结构：\n" + json.dumps(
                schema_hint, ensure_ascii=False
            )
        messages = [
            {"role": "system", "content": system + schema_text},
            {"role": "user", "content": user},
        ]

        def request(client: Any, model: str) -> Any:
            return client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
                timeout=self._timeout,
            )

        resp, model, attempts, fallback_from, degraded = self._request_with_policy(request)
        content = resp.choices[0].message.content
        if content is None or not content.strip():
            raise EmptyOutputError("模型返回空内容")
        payload = self._parse_json(content)
        if validator is not None:
            try:
                validator(payload)
            except ProviderError:
                raise
            except Exception as exc:  # noqa: BLE001 - 业务校验失败统一归类
                raise SchemaMismatchError(f"模型输出不符合 Schema: {exc}") from exc
        usage = getattr(resp, "usage", None)
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        meta = CallMeta(
            provider=self.engine,
            model=model,
            prompt_version=prompt_version,
            attempts=attempts,
            retries=max(0, attempts - 1),
            fallback_from=fallback_from,
            degraded_reason=degraded,
            prompt_tokens=pt,
            completion_tokens=ct,
            cost_estimate_cny=self._cost(model, pt, ct),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return CompletionResult(payload=payload, prompt_tokens=pt, completion_tokens=ct, meta=meta)

    def _request_with_policy(self, request: Callable[[Any, str], Any]) -> tuple[Any, str, int, str | None, str | None]:
        """按 retry + fallback 策略执行一次请求。

        返回 (response, 实际模型, 该模型上的尝试次数, 从哪个模型降级, 降级原因)。
        """
        deadline = time.monotonic() + self._total_deadline_s
        last_error: ProviderError | None = None
        fallback_from: str | None = None
        degraded_reason: str | None = None

        for model_index, model in enumerate(self._models):
            client = self._clients[model]
            attempts = 0
            for attempt in range(1, self._max_retries + 2):
                attempts = attempt
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    last_error = ProviderTimeout("总 Deadline 已到，停止重试")
                    break
                try:
                    with self._semaphore:
                        resp = request(client, model)
                    if fallback_from:
                        log_event(
                            logger,
                            "llm.fallback_ok",
                            model=model,
                            tokens=0,
                            latency=0,
                            status="ok",
                            device="api",
                            attempts=attempt,
                            fallback_from=fallback_from,
                        )
                    return resp, model, attempts, fallback_from, degraded_reason
                except Exception as exc:  # noqa: BLE001 - 统一分类
                    error = classify_exception(exc)
                    last_error = error
                    log_event(
                        logger,
                        "llm.attempt_failed",
                        model=model,
                        tokens=0,
                        latency=0,
                        status="failed",
                        device="api",
                        attempts=attempt,
                        retryable=error.retriable,
                        category=error.category,
                        status_code=error.status_code,
                        fallback_from=fallback_from,
                    )
                    if not error.retriable or attempt >= self._max_retries + 1:
                        break
                    delay = self._backoff_base_s * (2 ** (attempt - 1))
                    if time.monotonic() + delay >= deadline:
                        last_error = ProviderTimeout("退避将超过总 Deadline，停止重试")
                        break
                    self._sleep(delay)
            if last_error is not None and last_error.retriable and model_index + 1 < len(self._models):
                fallback_from = model
                degraded_reason = f"{model} 失败[{last_error.category}]，切换到备用模型"
                continue
            if last_error is not None:
                raise last_error
        raise last_error or ProviderError("未发起任何请求")

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        text = _strip_fence(content)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BadJSONError(f"模型未返回合法 JSON: {text[:200]}") from exc
        if not isinstance(payload, dict):
            raise SchemaMismatchError(f"模型返回的 JSON 顶层不是对象: {type(payload).__name__}")
        return payload

    def _cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
        """只有主模型 + 配置了单价时才估算；备用模型单价未知，宁可留空。"""
        if model != self.model:
            return None
        if not self._price_input or not self._price_output:
            return None
        return round(
            prompt_tokens / 1_000_000 * float(self._price_input)
            + completion_tokens / 1_000_000 * float(self._price_output),
            6,
        )
