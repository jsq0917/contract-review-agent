"""LangGraph State：累加字段用 reducer，派生视图用覆盖。"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from lexflow.models import Evidence, Finding, Verification


def merge_findings(left: list[Finding] | None, right: list[Finding] | None) -> list[Finding]:
    """两道 reducer：

    1. 按 ``finding_id`` 去重（同一分支重复写入、节点回写自身结果时幂等）；
    2. 再按 ``(clause_id, risk_type)`` 合并跨路重复发现。

    第 2 步必须放在 reducer 里而不是放到某个节点里：节点返回的是「合并后的完整列表」，
    但 reducer 只做并集，没被覆盖到的旧条目（另一路的原始 finding_id）会被 union 回来，
    导致合并结果被撤销。放在 reducer 里，两路写进来的那一刻就合并，不存在中间态。
    """
    by_id: dict[str, Finding] = {}
    for item in (left or []):
        by_id[item.finding_id] = item
    for item in (right or []):
        by_id[item.finding_id] = item
    merged = merge_duplicate_findings(list(by_id.values()))
    return sorted(merged, key=lambda item: item.finding_id)


_RISK_ORDER = {"high": 0, "medium": 1, "low": 2}


def merge_duplicate_findings(findings: list[Finding]) -> list[Finding]:
    """跨法务/商务合并同一 (clause_id, risk_type) 的发现，保留两路来源与文本。

    ``risk_type`` 为空或 ``other`` 的不合并——没有可靠的类型键时强行合并会把
    两个不同问题压成一条。这条边界是刻意的，不是遗漏。
    """
    groups: dict[tuple[str, str], list[Finding]] = {}
    kept: list[Finding] = []
    for finding in findings:
        risk_type = (finding.risk_type or "").strip()
        if not risk_type or risk_type == "other":
            kept.append(finding)
            continue
        groups.setdefault((finding.clause_id, risk_type), []).append(finding)

    merged = list(kept)
    for items in groups.values():
        if len(items) == 1:
            merged.append(items[0])
            continue
        ordered = sorted(
            items,
            key=lambda item: (
                _RISK_ORDER.get(item.risk_level, 9),
                0 if item.reviewer == "legal" else 1,
            ),
        )
        primary = ordered[0]
        reviewers: list[str] = []
        for item in ordered:
            for name in item.reviewers or [item.reviewer]:
                if name not in reviewers:
                    reviewers.append(name)
        issues = {item.issue.strip() for item in ordered if item.issue.strip()}
        suggestions = {item.suggestion.strip() for item in ordered if item.suggestion.strip()}
        if len(issues) == 1:
            issue = primary.issue
        else:
            issue = "；".join(
                f"[{'法务' if item.reviewer == 'legal' else '商务'}] {item.issue.strip()}"
                for item in ordered
            )
        if len(suggestions) == 1:
            suggestion = primary.suggestion
        else:
            suggestion = "；".join(
                f"[{'法务' if item.reviewer == 'legal' else '商务'}] {item.suggestion.strip()}"
                for item in ordered
            )
        update: dict[str, Any] = {"reviewers": reviewers, "issue": issue, "suggestion": suggestion}
        if not primary.law_name:
            cited = next((item for item in ordered if item.law_name and item.article_no), None)
            if cited is not None:
                update.update(
                    {
                        "law_name": cited.law_name,
                        "article_no": cited.article_no,
                        "cited_quote": cited.cited_quote,
                        "citation_reason": None,
                    }
                )
        if not primary.rule_id:
            rule = next((item.rule_id for item in ordered if item.rule_id), None)
            if rule:
                update["rule_id"] = rule
        merged.append(primary.model_copy(update=update))
    return merged


def merge_evidence(left: list[Evidence] | None, right: list[Evidence] | None) -> list[Evidence]:
    """证据账本按 law_name#article_no 去重；并行分支重复命中时保留分数更高者。"""
    merged: dict[str, Evidence] = {}
    for item in (left or []):
        key = f"{item.law_name}#{item.article_no}"
        merged[key] = item
    for item in (right or []):
        key = f"{item.law_name}#{item.article_no}"
        current = merged.get(key)
        if current is None or item.score >= current.score:
            merged[key] = item
    return sorted(merged.values(), key=lambda item: item.evidence_id)


def merge_verifications(
    left: list[Verification] | None, right: list[Verification] | None
) -> list[Verification]:
    merged: dict[tuple[str, str], Verification] = {}
    for item in (left or []):
        merged[(item.finding_id, item.gate)] = item
    for item in (right or []):
        merged[(item.finding_id, item.gate)] = item
    return [merged[key] for key in sorted(merged)]


def add_warnings(left: list[str] | None, right: list[str] | None) -> list[str]:
    result = list(left or [])
    for item in right or []:
        if item not in result:
            result.append(item)
    return result


def merge_dict(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(left or {})
    result.update(right or {})
    return result


class ReviewState(TypedDict, total=False):
    matter_id: str
    doc_path: str
    filename: str
    stance: str
    as_of: str
    engine_requested: str
    require_confirmation: bool
    auto_approve: bool

    parsed: dict[str, Any]
    clauses: list[dict[str, Any]]
    findings: Annotated[list[Finding], merge_findings]
    evidence: Annotated[list[Evidence], merge_evidence]
    verifications: Annotated[list[Verification], merge_verifications]
    warnings: Annotated[list[str], add_warnings]

    report: dict[str, Any]
    docx_path: str
    human_decision: dict[str, Any]
    node_timings: Annotated[dict[str, int], merge_dict]
    llm_calls: Annotated[int, operator.add]
    prompt_tokens: Annotated[int, operator.add]
    completion_tokens: Annotated[int, operator.add]
    retrieval_mode: str
    retrieval_device: str
    engine: str
    error: str
