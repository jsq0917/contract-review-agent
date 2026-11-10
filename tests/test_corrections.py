"""P0 修正：findings 复合主键、空输出归档、引用原因不静默缺失。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from lexflow.failures import read_failures
from lexflow.llm.base import LLMProvider
from lexflow.llm.mock import MockProvider
from lexflow.models import Clause, Finding
from lexflow.review.reviewer import review_clauses
from lexflow.storage.db import Store


def _finding(finding_id: str, matter_id: str) -> Finding:
    return Finding(
        finding_id=finding_id,
        clause_id="C0001",
        reviewer="legal",
        risk_level="medium",
        issue=f"issue-{matter_id}",
        suggestion="建议",
        original_quote="原文",
        engine="mock",
        reviewers=["legal"],
    )


def test_same_finding_id_isolated_by_matter(tmp_path: Path) -> None:
    store = Store(tmp_path / "store.db")
    store.save_matter("matter-a", "a.docx", "mock", "甲方", "2026-11-03")
    store.save_matter("matter-b", "b.docx", "mock", "甲方", "2026-11-03")
    store.replace_findings("matter-a", [_finding("F-legal-C0001-1", "a")])
    store.replace_findings("matter-b", [_finding("F-legal-C0001-1", "b")])

    assert store.count_findings("matter-a") == 1
    assert store.count_findings("matter-b") == 1
    assert store.list_findings("matter-a")[0]["issue"] == "issue-a"
    assert store.list_findings("matter-b")[0]["issue"] == "issue-b"
    store.close()


def test_legacy_single_pk_migrates_without_data_loss(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE findings (
            finding_id TEXT PRIMARY KEY,
            matter_id TEXT NOT NULL,
            clause_id TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            issue TEXT NOT NULL,
            suggestion TEXT NOT NULL,
            original_quote TEXT NOT NULL,
            cited_quote TEXT,
            law_name TEXT,
            article_no TEXT,
            evidence_id TEXT,
            rule_id TEXT,
            engine TEXT NOT NULL,
            doc_id TEXT NOT NULL DEFAULT '',
            locator TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO findings VALUES (
            'F-legal-C0001-1', 'm1', 'C0001', 'legal', 'medium', 'old', 's',
            'q', NULL, NULL, NULL, NULL, NULL, 'mock', '', ''
        );
        """
    )
    conn.commit()
    conn.close()

    store = Store(db)
    pk = [row["name"] for row in store.conn.execute("PRAGMA table_info(findings)") if row["pk"]]
    assert pk == ["matter_id", "finding_id"]
    assert store.count_findings("m1") == 1
    store.replace_findings("m2", [_finding("F-legal-C0001-1", "m2")])
    assert store.count_findings("m1") == 1
    assert store.count_findings("m2") == 1
    store.close()


class _StubEmbedder:
    resolved_device = "cpu"


class _StubRetriever:
    articles: list[Any] = []
    embedder = _StubEmbedder()

    def search(self, query: str, top_k: int = 6) -> list[Any]:
        return []

    def retrieve(self, query: str, top_k: int = 6, *, rerank: bool = False) -> list[Any]:
        return []


class _EmptyApiProvider(LLMProvider):
    engine = "api"
    model = "stub-empty"

    def complete_json(self, system: str, user: str, schema_hint: dict[str, Any] | None = None):
        return {"clause_id": "C0001", "findings": []}, 10, 0


def _clause() -> Clause:
    return Clause(
        clause_id="C0001",
        index=1,
        text="本合同一式两份，双方各执一份。",
        char_start=0,
        char_end=20,
    )


def test_api_empty_output_is_archived(tmp_path: Path) -> None:
    log = tmp_path / "failures.jsonl"
    from lexflow.failures import FailureLog

    failure_log = FailureLog(log)
    findings, warnings, _, _ = review_clauses(
        [_clause()], _StubRetriever(), _EmptyApiProvider(), "legal",
        matter_id="m-api", failure_log=failure_log,
    )
    assert findings == []
    records = read_failures(log)
    assert len(records) == 1
    assert records[0].category == "empty_output"
    assert records[0].retriable is True


def test_mock_normal_empty_is_not_archived(tmp_path: Path) -> None:
    log = tmp_path / "failures.jsonl"
    from lexflow.failures import FailureLog

    failure_log = FailureLog(log)
    findings, _, _, _ = review_clauses(
        [_clause()], _StubRetriever(), MockProvider(), "legal",
        matter_id="m-mock", failure_log=failure_log,
    )
    assert findings == []
    assert read_failures(log) == []


def test_uncited_finding_gets_explicit_reason(tmp_path: Path) -> None:
    payload = {
        "clause_id": "C0001",
        "findings": [
            {
                "risk_level": "medium",
                "risk_type": "payment",
                "issue": "付款条件不明确",
                "suggestion": "明确付款时间",
                "original_quote": "合同一式两份",
                "law_name": None,
                "article_no": None,
            }
        ],
    }

    class _Provider(LLMProvider):
        engine = "api"
        model = "stub-uncited"

        def complete_json(self, system: str, user: str, schema_hint: dict[str, Any] | None = None):
            return payload, 10, 5

    findings, _, _, _ = review_clauses(
        [_clause()], _StubRetriever(), _Provider(), "legal", matter_id="m-uncited"
    )
    assert len(findings) == 1
    assert findings[0].citation_reason == "模型未给出法条，也未说明原因"
    assert findings[0].reviewers == ["legal"]
