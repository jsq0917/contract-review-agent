"""失败归档：非法 JSON / 缺字段 / 引用落空都必须被拒绝并落盘。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lexflow.failures import FailureLog, read_failures, summarize
from lexflow.llm.base import LLMProvider
from lexflow.models import Clause
from lexflow.review.reviewer import review_clauses


def _clause() -> Clause:
    return Clause(
        clause_id="C0001",
        index=1,
        text="甲方应在合同签订后五个工作日内付款，且付款比例为合同总价的百分之百。",
        page_no=1,
        char_start=0,
        char_end=40,
        clause_type="payment",
    )


class InvalidJsonProvider(LLMProvider):
    engine = "mock"
    model = "broken-json"

    def complete_json(
        self, system: str, user: str, schema_hint: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], int, int]:
        raise ValueError("模型未返回合法 JSON: {not-json")


class MissingFieldProvider(LLMProvider):
    engine = "mock"
    model = "broken-schema"

    def complete_json(
        self, system: str, user: str, schema_hint: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], int, int]:
        return {"findings": [{"risk_level": "high", "issue": "缺 suggestion"}]}, 0, 0


class BadCitationProvider(LLMProvider):
    engine = "api"
    model = "bad-citation"

    def complete_json(
        self, system: str, user: str, schema_hint: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], int, int]:
        return {
            "findings": [
                {
                    "risk_level": "high",
                    "issue": "付款风险",
                    "suggestion": "绑定验收",
                    "original_quote": "甲方应在合同签订后五个工作日内付款",
                    "law_name": "中华人民共和国民法典",
                    "article_no": "第九千九百九十九条",
                }
            ]
        }, 1, 1


class UnknownRuleIdProvider(LLMProvider):
    engine = "mock"
    model = "broken-rule"

    def complete_json(
        self, system: str, user: str, schema_hint: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], int, int]:
        return {
            "findings": [
                {
                    "risk_level": "high",
                    "issue": "付款风险",
                    "suggestion": "绑定验收",
                    "original_quote": "甲方应在合同签订后五个工作日内付款",
                    "rule_id": "R-UNKNOWN",
                }
            ]
        }, 0, 0


def _review(provider: LLMProvider, service, failure_path: Path):
    log = FailureLog(failure_path)
    return review_clauses(
        [_clause()],
        service.retriever,
        provider,
        "legal",
        matter_id="M-TEST",
        failure_log=log,
    )


def test_invalid_json_is_archived(service, tmp_path: Path) -> None:
    path = tmp_path / "failures.jsonl"
    findings, warnings, _pt, _ct = _review(InvalidJsonProvider(), service, path)
    assert findings == []
    assert warnings and "invalid_json" in warnings[0]
    records = read_failures(path)
    assert len(records) == 1
    item = records[0]
    assert item.matter_id == "M-TEST"
    assert item.node == "review_legal"
    assert item.category == "invalid_json"
    assert item.retriable is False
    assert item.attempt == 1
    assert len(item.raw_output_hash) == 64
    assert "模型未返回合法 JSON" in item.reason


def test_missing_fields_are_rejected(service, tmp_path: Path) -> None:
    path = tmp_path / "failures.jsonl"
    findings, warnings, _pt, _ct = _review(MissingFieldProvider(), service, path)
    assert findings == []
    assert warnings
    assert summarize(path)["by_category"] == {"schema_invalid": 1}


def test_citation_not_found_is_rejected(service, tmp_path: Path) -> None:
    path = tmp_path / "failures.jsonl"
    findings, warnings, _pt, _ct = _review(BadCitationProvider(), service, path)
    assert findings == []
    assert "citation_not_found" in warnings[0]
    assert summarize(path)["by_category"] == {"citation_not_found": 1}
    raw = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert raw["retriable"] is True


def test_mock_unknown_rule_id_is_rejected(service, tmp_path: Path) -> None:
    path = tmp_path / "failures.jsonl"
    findings, warnings, _pt, _ct = _review(UnknownRuleIdProvider(), service, path)
    assert findings == []
    assert "schema_invalid" in warnings[0]
    assert summarize(path)["total"] == 1
