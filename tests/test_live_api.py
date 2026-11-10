"""真实 API 冒烟：默认跳过，只有显式设置 LEXFLOW_LIVE_API=1 且配置 Key 才执行。

独立命令::

    LEXFLOW_LIVE_API=1 uv run pytest -q -m live

不放入默认 CI：每次运行都会产生真实费用。
"""

from __future__ import annotations

import os

import pytest

from lexflow.config import get_settings
from lexflow.llm.factory import build_provider

pytestmark = pytest.mark.live

ENABLED = os.getenv("LEXFLOW_LIVE_API") == "1" and bool(os.getenv("LEXFLOW_API_KEY"))


@pytest.mark.skipif(not ENABLED, reason="需要 LEXFLOW_LIVE_API=1 与 LEXFLOW_API_KEY")
def test_live_provider_returns_json():
    provider = build_provider(get_settings(), "api")
    payload, prompt_tokens, completion_tokens = provider.complete_json(
        "你是 JSON 生成器，只输出 JSON，不要解释。",
        '输出 {"ok": true}',
    )
    assert payload.get("ok") is True
    assert prompt_tokens >= 0 and completion_tokens >= 0
