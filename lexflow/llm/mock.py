"""离线确定性 Provider。

mock 输出只用于验证链路：规则来自 lexflow.review.rules 的显式规则表，
不做法律推理，也不参与真实效果指标。
"""

from __future__ import annotations

import json
import re
from typing import Any

from lexflow.llm.base import CallMeta, LLMProvider, ToolCallRequest, ToolCallResponse
from lexflow.review.rules import detect_rules


def _best_sentence(text: str, reference: str) -> str:
    """挑与条款重合度最高的一句，保证引文是原文的连续子串。"""
    sentences = [item.strip() for item in re.split(r"(?<=[。；！？])", text) if item.strip()]
    if not sentences:
        return text.strip()
    ref_chars = set(reference)

    def score(sentence: str) -> float:
        chars = set(sentence)
        return len(chars & ref_chars) / max(1, len(chars))

    return max(sentences, key=score)


class MockProvider(LLMProvider):
    engine = "mock"
    model = "mock-deterministic-v2"

    def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        tool_choice: str = "auto",
        prompt_version: str = "",
    ) -> ToolCallResponse:
        """确定性的三段式工具调用：检索 → 读条文 → 给结论。

        这是离线 fixture：真实 API 是否调用工具由模型决定；这里只保证链路、
        参数校验、步数上限与拒引分支都能在无 Key 环境下被测试覆盖。
        """
        user = next((item for item in messages if item.get("role") == "user"), None)
        context = json.loads(user["content"]) if user else {}
        tool_messages = [item for item in messages if item.get("role") == "tool"]
        meta = CallMeta(
            provider=self.engine,
            model=self.model,
            prompt_version=prompt_version,
            latency_ms=0,
        )

        if not tool_messages:
            call = ToolCallRequest(
                call_id="mock-call-1",
                name="search_statutes",
                arguments={
                    "query": str(context.get("query", ""))[:500],
                    "top_k": 3,
                },
            )
            return ToolCallResponse(tool_calls=[call], meta=meta)

        last = json.loads(tool_messages[-1]["content"])
        if len(tool_messages) == 1:
            results = last.get("results") or []
            if not results:
                return self._answer(
                    {"status": "no_applicable_law", "reason": "检索无候选，拒绝强行引用"}, meta
                )
            top = results[0]
            call = ToolCallRequest(
                call_id="mock-call-2",
                name="get_article",
                arguments={
                    "law_name": top.get("law_name", ""),
                    "article_no": top.get("article_no", ""),
                },
            )
            return ToolCallResponse(tool_calls=[call], meta=meta)

        if not last.get("ok"):
            return self._answer(
                {
                    "status": "no_applicable_law",
                    "reason": f"条号不存在或读取失败: {last.get('error', 'unknown')}",
                },
                meta,
            )
        text = str(last.get("text", ""))
        reference = f"{context.get('clause_text', '')} {context.get('issue', '')}"
        return self._answer(
            {
                "status": "resolved",
                "law_name": last.get("law_name"),
                "article_no": last.get("article_no"),
                "cited_quote": _best_sentence(text, reference),
                "reason": "检索命中且原文可逐字核对",
                "confidence": "medium",
            },
            meta,
        )

    @staticmethod
    def _answer(payload: dict[str, Any], meta: CallMeta) -> ToolCallResponse:
        return ToolCallResponse(payload=payload, meta=meta, raw_content=json.dumps(payload, ensure_ascii=False))

    def complete_json(
        self,
        system: str,
        user: str,
        schema_hint: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], int, int]:
        # 从 user prompt 中提取条款编号、类型与文本；不调用任何模型。
        clause_id = ""
        clause_type = "unknown"
        stance = "甲方"
        for line in user.splitlines():
            if line.startswith("CLAUSE_ID:"):
                clause_id = line.split(":", 1)[1].strip()
            elif line.startswith("CLAUSE_TYPE:"):
                clause_type = line.split(":", 1)[1].strip()
            elif line.startswith("STANCE:"):
                stance = line.split(":", 1)[1].strip() or "甲方"
        if "TEXT:" in user:
            text = user.split("TEXT:", 1)[1].split("候选法条", 1)[0].strip()
        else:
            text = user

        findings = [
            {
                "risk_level": match.rule.risk_level,
                "issue": match.rule.issue,
                "suggestion": match.rule.suggestion,
                "original_quote": match.quote,
                "rule_id": match.rule.rule_id,
                "risk_type": match.rule.risk_type,
            }
            for match in detect_rules(text, clause_type, stance)
        ]
        # 规则未命中时返回空数组，不生成“未发现风险”这种伪结论。
        payload = {"clause_id": clause_id, "findings": findings}
        # mock 不产生真实 token 消耗，计数为 0，避免伪造成本。
        return payload, 0, 0
