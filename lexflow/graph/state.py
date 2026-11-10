"""LangGraph State：累加字段用 reducer，派生视图用覆盖。"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from lexflow.models import Evidence, Finding, Verification
from lexflow.verify.gates import normalize_quote


def merge_findings(left: list[Finding] | None, right: list[Finding] | None) -> list[Finding]:
    """两道 reducer：

    1. 按 ``finding_id`` 去重（同一分支重复写入、节点回写自身结果时幂等）；
    2. 再按 ``(clause_id, risk_type, 引文)`` 合并跨路重复发现（引文不同不合并）。

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


def _quotes_related(left: str, right: str) -> bool:
    """两段引文是否指向同一句话：归一化后互相包含，或完全相同。"""
    a, b = normalize_quote(left), normalize_quote(right)
    if not a or not b:
        return False
    return a in b or b in a


def _union_reviewers(items: list[Finding]) -> list[str]:
    reviewers: list[str] = []
    for item in items:
        for name in item.reviewers or [item.reviewer]:
            if name not in reviewers:
                reviewers.append(name)
    return reviewers


def _merge_cluster(items: list[Finding]) -> Finding:
    """合并同一句话上的多条发现。

    前提是它们的 ``original_quote`` 互相包含（由调用方保证），所以只保留
    一份引文不会把两个不同风险拼成一条。
    """
    ordered = sorted(
        items,
        key=lambda item: (
            _RISK_ORDER.get(item.risk_level, 9),
            0 if item.reviewer == "legal" else 1,
        ),
    )
    primary = ordered[0]
    reviewers = _union_reviewers(ordered)
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
    return primary.model_copy(update=update)


def merge_duplicate_findings(findings: list[Finding]) -> list[Finding]:
    """跨法务/商务合并同一句话上的发现，保留两路来源与文本。

    合并键是 ``(clause_id, risk_type, 引文)``，而不是只按 ``(clause_id, risk_type)``：
    同一条款可能同时写了工伤、婚育等两个子风险（risk_type 都是 termination），
    只按类型合并会把两段 issue 拼到一份引文上，产生「引文只支撑一半结论」的
    嵌合体。这里要求两条发现的原引文互相包含才合并。

    ``risk_type`` 为空或 ``other`` 时不做跨路合并；只有在引文与 issue 都归一化
    相同（即完全重复）时才去重，避免丢掉另一路的来源标记。
    """
    buckets: dict[tuple[str, str, str], list[Finding]] = {}
    for finding in findings:
        risk_type = (finding.risk_type or "").strip()
        if not risk_type or risk_type == "other":
            key = (
                finding.clause_id,
                "other",
                f"{normalize_quote(finding.original_quote)}\x00{normalize_quote(finding.issue)}",
            )
        else:
            key = (finding.clause_id, risk_type, "")
        buckets.setdefault(key, []).append(finding)

    merged: list[Finding] = []
    for key, items in buckets.items():
        if key[1] == "other":
            if len(items) == 1:
                merged.append(items[0])
            else:
                merged.append(
                    items[0].model_copy(update={"reviewers": _union_reviewers(items)})
                )
            continue
        clusters: list[list[Finding]] = []
        for item in items:
            for cluster in clusters:
                if _quotes_related(item.original_quote, cluster[0].original_quote):
                    cluster.append(item)
                    break
            else:
                clusters.append([item])
        for cluster in clusters:
            merged.append(cluster[0] if len(cluster) == 1 else _merge_cluster(cluster))
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
    # 本次审查是否使用精排。写进 state 而不是读全局设置：同一进程可能并发跑不同请求，
    # 且中断恢复后要求这次审查的参数仍然可追溯。
    rerank: bool
    # 请求级 trace：matter 内唯一，跨进程恢复时沿用同一个值。
    trace_id: str
    model_name: str
    prompt_version: str
    corpus_version: str
    # 本次跑哪几路审查（legal / commercial）；单路对照实验会把它收成一个。
    channels: list[str]

    parsed: dict[str, Any]
    clauses: list[dict[str, Any]]
    findings: Annotated[list[Finding], merge_findings]
    evidence: Annotated[list[Evidence], merge_evidence]
    verifications: Annotated[list[Verification], merge_verifications]
    warnings: Annotated[list[str], add_warnings]
    # 每次 LLM / 工具调用的治理信息；只增不减。
    llm_calls_meta: Annotated[list[dict[str, Any]], operator.add]
    tool_calls: Annotated[list[dict[str, Any]], operator.add]
    citation_resolutions: Annotated[list[dict[str, Any]], operator.add]

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
