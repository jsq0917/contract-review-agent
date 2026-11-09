"""OpenAI 兼容 API Provider（DeepSeek/Qwen 等）。

没有 API key 时构造会抛错；调用方应先走 factory 的 mock 降级。
"""

from __future__ import annotations

import json
from typing import Any

from lexflow.llm.base import EmptyOutputError, LLMProvider


class OpenAICompatibleProvider(LLMProvider):
    engine = "api"

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        if not api_key:
            raise ValueError("缺少 LEXFLOW_API_KEY，无法使用 api engine")
        from openai import OpenAI

        self.model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def complete_json(
        self,
        system: str,
        user: str,
        schema_hint: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], int, int]:
        schema_text = ""
        if schema_hint:
            schema_text = "\n请严格输出 JSON，且符合以下结构：\n" + json.dumps(
                schema_hint, ensure_ascii=False
            )
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system + schema_text},
                {"role": "user", "content": user},
            ],
            temperature=0,
        )
        content = resp.choices[0].message.content
        if content is None or not content.strip():
            raise EmptyOutputError("模型返回空内容")
        content = content.strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:]
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"模型未返回合法 JSON: {content[:200]}") from exc
        usage = resp.usage
        return payload, int(usage.prompt_tokens if usage else 0), int(usage.completion_tokens if usage else 0)
