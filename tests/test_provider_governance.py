"""Provider 调用治理：重试、fallback、超时、错误分类、Schema 校验。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lexflow.llm.errors import (
    AuthError,
    BadJSONError,
    EmptyOutputError,
    ProviderTimeout,
    SchemaMismatchError,
    ServerError,
)
from lexflow.llm.openai_compat import OpenAICompatibleProvider


class FakeAPIError(Exception):
    def __init__(self, status_code: int, message: str = "upstream error") -> None:
        super().__init__(message)
        self.status_code = status_code


def _response(content: str, prompt_tokens: int = 10, completion_tokens: int = 5):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


class FakeClient:
    """按脚本返回响应或抛错；脚本项可以是 Exception 实例或 callable(model)。"""

    def __init__(self, script, recorder: list | None = None) -> None:
        self.script = list(script)
        self.calls: list[dict] = []
        self._recorder = recorder if recorder is not None else self.calls
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self._recorder.append(kwargs)
        if not self.script:
            raise AssertionError("FakeClient 脚本已耗尽")
        item = self.script.pop(0)
        if callable(item) and not isinstance(item, BaseException):
            item = item(kwargs.get("model"))
        if isinstance(item, BaseException):
            raise item
        return item


def _provider(script, **kwargs):
    sleeps: list[float] = []
    client = FakeClient(script)
    provider = OpenAICompatibleProvider(
        api_key="sk-test-not-real",
        base_url="http://localhost:1",
        model="primary-model",
        client=client,
        sleep=sleeps.append,
        **kwargs,
    )
    return provider, client, sleeps


def test_retry_on_429_then_success():
    provider, client, sleeps = _provider(
        [FakeAPIError(429), _response('{"findings": []}')],
        max_retries=2,
        backoff_base_s=0.1,
    )
    result = provider.complete_json_with_meta("s", "u")
    assert result.meta.attempts == 2
    assert result.meta.retries == 1
    assert result.meta.status == "ok"
    assert len(client.calls) == 2
    assert sleeps == [0.1]


def test_auth_error_is_not_retried():
    provider, client, _ = _provider([FakeAPIError(401)], max_retries=3)
    with pytest.raises(AuthError):
        provider.complete_json("s", "u")
    assert len(client.calls) == 1


def test_bad_request_is_not_retried():
    provider, client, _ = _provider([FakeAPIError(400)], max_retries=3)
    with pytest.raises(Exception) as info:
        provider.complete_json("s", "u")
    assert getattr(info.value, "category", "") == "bad_request"
    assert len(client.calls) == 1


def test_server_error_exhausts_retries():
    provider, client, _ = _provider(
        [FakeAPIError(500), FakeAPIError(502), FakeAPIError(503)],
        max_retries=2,
        backoff_base_s=0,
    )
    with pytest.raises(ServerError):
        provider.complete_json("s", "u")
    assert len(client.calls) == 3


def test_total_deadline_stops_before_request():
    provider, client, _ = _provider([_response("{}")], total_deadline_s=0)
    with pytest.raises(ProviderTimeout):
        provider.complete_json("s", "u")
    assert client.calls == []


def test_empty_output_and_bad_json_are_distinct():
    provider, _, _ = _provider([_response("   ")], max_retries=0)
    with pytest.raises(EmptyOutputError):
        provider.complete_json("s", "u")

    provider, _, _ = _provider([_response("not json")], max_retries=0)
    with pytest.raises(BadJSONError):
        provider.complete_json("s", "u")


def test_schema_mismatch_is_its_own_error():
    provider, _, _ = _provider([_response('{"findings": "oops"}')], max_retries=0)

    def validator(payload):
        if not isinstance(payload.get("findings"), list):
            raise ValueError("findings 必须是数组")

    with pytest.raises(SchemaMismatchError):
        provider.complete_json_with_meta("s", "u", validator=validator)


def test_fallback_model_is_used_and_reported():
    def script(model: str):
        if model == "primary-model":
            return FakeAPIError(500)
        return _response('{"findings": []}')

    provider, client, _ = _provider(
        [script, script],
        fallback_models=("backup-model",),
        max_retries=0,
    )
    result = provider.complete_json_with_meta("s", "u")
    assert result.meta.model == "backup-model"
    assert result.meta.fallback_from == "primary-model"
    assert "切换" in (result.meta.degraded_reason or "")
    assert [call["model"] for call in client.calls] == ["primary-model", "backup-model"]
    # 备用模型单价未知，不猜金额。
    assert result.meta.cost_estimate_cny is None


def test_cost_only_when_prices_configured():
    provider, _, _ = _provider(
        [_response("{}", 1_000_000, 1_000_000)],
        max_retries=0,
        price_input_per_m="2",
        price_output_per_m="8",
    )
    result = provider.complete_json_with_meta("s", "u")
    assert result.meta.cost_estimate_cny == pytest.approx(10.0)


def _tool_response(arguments: str):
    message = SimpleNamespace(
        content=None,
        tool_calls=[
            SimpleNamespace(
                id="call-1",
                function=SimpleNamespace(name="search_statutes", arguments=arguments),
            )
        ],
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
    )


def test_complete_with_tools_parses_arguments():
    provider, _, _ = _provider([_tool_response('{"query": "劳动合同"}')], max_retries=0)
    result = provider.complete_with_tools([{"role": "user", "content": "x"}], [])
    assert result.tool_calls[0].name == "search_statutes"
    assert result.tool_calls[0].arguments == {"query": "劳动合同"}
    assert result.meta is not None and result.meta.prompt_tokens == 7


def test_complete_with_tools_rejects_bad_arguments_json():
    provider, _, _ = _provider([_tool_response("{not json")], max_retries=0)
    with pytest.raises(BadJSONError):
        provider.complete_with_tools([{"role": "user", "content": "x"}], [])
