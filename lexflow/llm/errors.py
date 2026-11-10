"""Provider 错误分类：决定「能不能重试」与「失败该记成哪一类」。

分类只看错误本身，不看调用方；重试策略在 ``openai_compat.py`` 里按
``retriable`` 执行。鉴权错误、参数错误永远不重试——重试只会把同一个错误
再打一次上游，既浪费额度，也会掩盖真正的原因。
"""

from __future__ import annotations

from typing import Any


class ProviderError(RuntimeError):
    """Provider 层错误的基类。"""

    category = "provider_error"
    retriable = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retriable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        if retriable is not None:
            self.retriable = retriable


class EmptyOutputError(ProviderError):
    """模型返回空内容；与业务上的 findings=[] 区分。"""

    category = "empty_output"
    retriable = True


class BadJSONError(ProviderError):
    """返回了内容，但不是合法 JSON。"""

    category = "bad_json"
    retriable = True


class SchemaMismatchError(ProviderError):
    """是合法 JSON，但不符合声明的 Schema。"""

    category = "schema_mismatch"
    retriable = True


class ProviderTimeout(ProviderError):
    """连接/读取/总 Deadline 超时。"""

    category = "timeout"
    retriable = True


class RateLimitError(ProviderError):
    category = "rate_limit"
    retriable = True


class ServerError(ProviderError):
    category = "server_error"
    retriable = True


class ConnectionFailed(ProviderError):
    category = "connection_error"
    retriable = True


class AuthError(ProviderError):
    category = "auth_error"
    retriable = False


class BadRequestError(ProviderError):
    category = "bad_request"
    retriable = False


class NotImplementedProviderError(ProviderError):
    category = "not_implemented"
    retriable = False


def classify_exception(exc: BaseException) -> ProviderError:
    """把 openai SDK 或任意异常映射成 ProviderError。

    不 ``import openai`` 做 isinstance：测试与降级路径不应该强依赖 SDK，
    这里按类名 + ``status_code`` 判断，够用且不会漏掉包装过的异常。
    """
    if isinstance(exc, ProviderError):
        return exc
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__.lower()
    message = str(exc)
    if status == 401 or status == 403 or "authentication" in name or "permission" in name:
        return AuthError(message, status_code=status)
    if status == 429 or "ratelimit" in name:
        return RateLimitError(message, status_code=status)
    if (isinstance(status, int) and 500 <= status < 600) or "internalserver" in name:
        return ServerError(message, status_code=status)
    if isinstance(status, int) and 400 <= status < 500:
        return BadRequestError(message, status_code=status)
    if isinstance(exc, TimeoutError) or "timeout" in name:
        return ProviderTimeout(message, status_code=status)
    if "connection" in name or "network" in name:
        return ConnectionFailed(message, status_code=status)
    return ProviderError(message, status_code=status)


def error_fields(exc: BaseException) -> dict[str, Any]:
    """写日志/失败归档用的稳定字段，不含任何密钥。"""
    error = classify_exception(exc)
    return {
        "category": error.category,
        "retriable": error.retriable,
        "status_code": error.status_code,
        "error_type": type(exc).__name__,
    }
