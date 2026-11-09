"""离线确定性 Provider。

mock 输出只用于验证链路：规则来自 lexflow.review.rules 的显式规则表，
不做法律推理，也不参与真实效果指标。
"""

from __future__ import annotations

from typing import Any

from lexflow.llm.base import LLMProvider
from lexflow.review.rules import detect_rules


class MockProvider(LLMProvider):
    engine = "mock"
    model = "mock-deterministic-v2"

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
