"""评测口径的单元测试：这些数字会进 README，算法必须先被钉住。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))

import metrics  # noqa: E402


def test_strict_traceable_requires_substring_of_clause_text():
    clauses = {"C0001": "甲方应在收到发票后  90 日内付款。"}
    ok = {"clause_id": "C0001", "original_quote": "收到发票后 90 日内付款"}
    fabricated = {"clause_id": "C0001", "original_quote": "甲方应在 30 日内付款"}
    assert metrics.strict_traceable(ok, clauses)
    assert not metrics.strict_traceable(fabricated, clauses)


def test_strict_traceable_ignores_punctuation_and_spaces():
    clauses = {"C0001": "乙方结婚或生育的，视为自动离职。"}
    finding = {"clause_id": "C0001", "original_quote": "乙方结婚或生育的 视为自动离职"}
    assert metrics.strict_traceable(finding, clauses)


def test_gates_summary_does_not_count_skipped_as_pass():
    findings = [{"finding_id": "F1", "law_name": "民法典", "article_no": "第五百八十五条"}]
    statuses = ["pass", "pass", "pass", "skipped", "pass"]
    verifications = [
        {"finding_id": "F1", "gate": gate, "passed": status == "pass", "status": status}
        for gate, status in zip(metrics.GATES, statuses)
    ]
    summary = metrics.gates_summary(findings, verifications)
    assert summary["all_pass"] == 0
    assert summary["skipped_findings"] == 1
    assert summary["cited"] == 1


def test_gates_summary_accepts_legacy_passed_only():
    findings = [{"finding_id": "F1", "law_name": None, "article_no": None}]
    verifications = [
        {"finding_id": "F1", "gate": gate, "passed": True} for gate in metrics.GATES
    ]
    summary = metrics.gates_summary(findings, verifications)
    assert summary["all_pass"] == 1
    assert summary["uncited"] == 1


def test_dual_review_stats():
    findings = [
        {"clause_id": "C1", "original_quote": "甲方应在 90 日内付款。", "reviewers": ["legal", "commercial"]},
        {"clause_id": "C2", "original_quote": "乙方不得解除合同。", "reviewers": ["legal"]},
        {"clause_id": "C3", "original_quote": "验收标准由甲方单方确定。", "reviewer": "commercial"},
    ]
    stats = metrics.dual_review_stats(findings)
    assert stats == {
        "raw_findings": 3,
        "risk_points": 3,
        "merged_both": 1,
        "legal_only": 1,
        "commercial_only": 1,
        "duplicates": 0,
    }
