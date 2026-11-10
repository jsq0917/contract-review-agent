"""条款审查：法务/商务两路，同一接口不同 prompt。

审查节点会先检索候选法条，要求模型只从候选中引用；候选之外不得编造。
模型输出必须通过 Pydantic 结构校验与引用存在性校验；失败写入 failures.jsonl，
不静默丢弃，也不给缺字段补默认值。
"""

from __future__ import annotations

import time
from typing import Any

from lexflow.failures import FailureLog, classify_exception, hash_payload
from lexflow.llm.base import LLMProvider
from lexflow.logging import get_logger, log_event
from lexflow.models import Clause, Finding
from lexflow.retrieval.hybrid import HybridRetriever
from lexflow.review.rules import RULES_BY_ID
from lexflow.review.schema import (
    CitationNotFound,
    SchemaViolation,
    ReviewPayload,
    validate_citations,
    validate_payload,
)

logger = get_logger("lexflow.review")

RISK_TYPES = (
    "payment",
    "payment_liability_exemption",
    "penalty_cap",
    "termination",
    "jurisdiction",
    "confidentiality",
    "force_majeure",
    "liability_cap",
    "acceptance_standard",
    "delivery",
    "other",
)

LEGAL_SYSTEM = """你是合同审查中的法务审查员。只输出 JSON，不要输出解释。
任务：找出条款中的重大法律风险（效力、违约责任、解除、举证、合规）。
判定标准：只报“有具体不利后果、且能在原条款中定位”的风险；常规且无明显不利后果的条款返回空数组，不要为每个条款强行给结论；同一风险类型只报最严重的一条。
要求：
1. 每条 finding 必须给 risk_type，取值：payment / payment_liability_exemption / penalty_cap / termination / jurisdiction / confidentiality / force_majeure / liability_cap / acceptance_standard / delivery / other。
2. 只能引用用户消息中给出的候选法条，不得引用候选之外的条号。
3. 有合适法条时给 law_name/article_no，cited_quote 必须从候选法条的 quote 字段逐字复制。
4. 找不到合适法条时 law_name/article_no 置 null，并在 citation_reason 写明原因（如“候选法条不覆盖该风险”），不得编造，也不得用不相关法条硬凑。
5. 每个风险必须引用原文片段 original_quote，不得改写原文。
6. 没有明显风险时 findings 返回空数组。
"""

COMMERCIAL_SYSTEM = """你是合同审查中的商务审查员。只输出 JSON，不要输出解释。
任务：找出条款中的重大商务风险（付款、交付、验收、价格、责任分配、单方权利）。
判定标准：只报“有具体不利后果、且能在原条款中定位”的风险；常规且无明显不利后果的条款返回空数组，不要为每个条款强行给结论；同一风险类型只报最严重的一条。
要求：
1. 每条 finding 必须给 risk_type，取值：payment / payment_liability_exemption / penalty_cap / termination / jurisdiction / confidentiality / force_majeure / liability_cap / acceptance_standard / delivery / other。
2. 只能引用用户消息中给出的候选法条，不得引用候选之外的条号。
3. 有合适法条时给 law_name/article_no，cited_quote 必须从候选法条的 quote 字段逐字复制。
4. 找不到合适法条时 law_name/article_no 置 null，并在 citation_reason 写明原因（如“候选法条不覆盖该风险”），不得编造，也不得用不相关法条硬凑。
5. 每个风险必须引用原文片段 original_quote，不得改写原文。
6. 没有明显风险时 findings 返回空数组。
"""

SCHEMA_HINT = {
    "findings": [
        {
            "risk_level": "high|medium|low",
            "risk_type": "payment|payment_liability_exemption|penalty_cap|termination|jurisdiction|confidentiality|force_majeure|liability_cap|acceptance_standard|delivery|other",
            "issue": "string",
            "suggestion": "string",
            "original_quote": "string",
            "law_name": "string|null",
            "article_no": "string|null",
            "cited_quote": "string|null",
            "citation_reason": "string|null（无合适法条时必填）",
            "rule_id": "string|null",
        }
    ]
}


def _candidate_text(hits: list[tuple[Any, float]]) -> str:
    lines: list[str] = []
    for article, _score in hits:
        quote = article.text[:500]
        lines.append(
            f"- law_name={article.law_name}; article_no={article.article_no}; quote={quote}"
        )
    return "\n".join(lines) if lines else "（无候选法条）"


def _known_articles(retriever: HybridRetriever) -> set[tuple[str, str]]:
    return {(article.law_name, article.article_no) for article in retriever.articles}


def _resolve_mock_citations(payload: ReviewPayload) -> ReviewPayload:
    """mock 只给 rule_id，法条由规则表决定，避免模型编造。"""
    resolved = []
    for draft in payload.findings:
        if not draft.rule_id or draft.rule_id not in RULES_BY_ID:
            raise SchemaViolation(f"mock 规则 ID 不存在: {draft.rule_id}")
        rule = RULES_BY_ID[draft.rule_id]
        resolved.append(
            draft.model_copy(
                update={
                    "law_name": rule.law_name,
                    "article_no": rule.article_no,
                    "risk_type": rule.risk_type,
                }
            )
        )
    return ReviewPayload(clause_id=payload.clause_id, findings=resolved)


def _normalize_risk_types(payload: ReviewPayload, clause_type: str) -> ReviewPayload:
    """未知/缺失的 risk_type 落 other；用于跨路合并与统计，不影响法条核验。"""
    normalized = []
    for draft in payload.findings:
        risk_type = (draft.risk_type or "").strip()
        if risk_type not in RISK_TYPES:
            risk_type = clause_type if clause_type in RISK_TYPES else "other"
        normalized.append(draft.model_copy(update={"risk_type": risk_type}))
    return ReviewPayload(clause_id=payload.clause_id, findings=normalized)


def _normalize_citation_reason(payload: ReviewPayload) -> ReviewPayload:
    """无引用的 finding 必须留下原因，不允许静默缺失。"""
    normalized = []
    for draft in payload.findings:
        reason = (draft.citation_reason or "").strip()
        if not draft.law_name and not draft.article_no and not reason:
            reason = "模型未给出法条，也未说明原因"
        normalized.append(draft.model_copy(update={"citation_reason": reason or None}))
    return ReviewPayload(clause_id=payload.clause_id, findings=normalized)


def review_clauses(
    clauses: list[Clause],
    retriever: HybridRetriever,
    provider: LLMProvider,
    reviewer: str,
    *,
    matter_id: str = "",
    failure_log: FailureLog | None = None,
    stance: str = "甲方",
    top_k: int = 3,
    rerank: bool = False,
) -> tuple[list[Finding], list[str], int, int]:
    """返回 (findings, warnings, prompt_tokens, completion_tokens)。

    ``rerank=True`` 时候选法条先经 CrossEncoder 精排。法务与商务两路传入的 query
    完全相同，精排结果由 retriever 的单飞缓存复用，不会算两遍。
    """
    system = LEGAL_SYSTEM if reviewer == "legal" else COMMERCIAL_SYSTEM
    findings: list[Finding] = []
    warnings: list[str] = []
    prompt_tokens = 0
    completion_tokens = 0
    known_articles = _known_articles(retriever)
    device = retriever.embedder.resolved_device or "n/a"

    for clause in clauses:
        clause_start = time.perf_counter()
        hits = retriever.retrieve(clause.text[:600], top_k, rerank=rerank)
        user = (
            f"CLAUSE_ID: {clause.clause_id}\n"
            f"CLAUSE_TYPE: {clause.clause_type}\n"
            f"STANCE: {stance}\n"
            f"TEXT: {clause.text[:800]}\n\n"
            f"候选法条：\n{_candidate_text(hits)}"
        )
        raw_payload: Any = None
        try:
            raw_payload, pt, ct = provider.complete_json(system, user, SCHEMA_HINT)
            prompt_tokens += pt
            completion_tokens += ct
        except Exception as exc:
            category = classify_exception(exc)
            reason = f"{clause.clause_id}: {type(exc).__name__}: {exc}"
            if failure_log is not None:
                failure_log.record(
                    matter_id=matter_id,
                    node=f"review_{reviewer}",
                    reason=reason[:500],
                    raw_output_hash=hash_payload(str(exc)),
                    retriable=provider.engine == "api",
                    category=category,
                )
            warnings.append(f"{clause.clause_id} {reviewer} 审查失败[{category}]: {reason[:200]}")
            log_event(
                logger,
                "review.failed",
                matter_id=matter_id,
                node=f"review_{reviewer}",
                model=provider.model,
                tokens=0,
                latency=int((time.perf_counter() - clause_start) * 1000),
                status="failed",
                device=device,
                clause_id=clause.clause_id,
                reviewer=reviewer,
                category=category,
            )
            continue

        try:
            payload = validate_payload(raw_payload)
            if provider.engine == "mock":
                payload = _resolve_mock_citations(payload)
            payload = _normalize_risk_types(payload, clause.clause_type)
            payload = _normalize_citation_reason(payload)
            validate_citations(payload, known_articles)
        except (SchemaViolation, CitationNotFound) as exc:
            category = classify_exception(exc)
            reason = f"{clause.clause_id}: {exc}"
            if failure_log is not None:
                failure_log.record(
                    matter_id=matter_id,
                    node=f"review_{reviewer}",
                    reason=reason[:500],
                    raw_output_hash=hash_payload(raw_payload),
                    retriable=provider.engine == "api",
                    category=category,
                )
            warnings.append(f"{clause.clause_id} {reviewer} 输出被拒绝[{category}]: {reason[:200]}")
            log_event(
                logger,
                "review.failed",
                matter_id=matter_id,
                node=f"review_{reviewer}",
                model=provider.model,
                tokens=0,
                latency=int((time.perf_counter() - clause_start) * 1000),
                status="failed",
                device=device,
                clause_id=clause.clause_id,
                reviewer=reviewer,
                category=category,
            )
            continue

        for index, item in enumerate(payload.findings, start=1):
            cited_quote = str(item.cited_quote or "")
            if provider.engine == "mock":
                # mock 直接从语料取原文，确保 G2 逐字匹配的输入真实可核。
                for article in retriever.articles:
                    if article.law_name == item.law_name and article.article_no == item.article_no:
                        cited_quote = article.text
                        break
            findings.append(
                Finding(
                    finding_id=f"F-{reviewer}-{clause.clause_id}-{index}",
                    clause_id=clause.clause_id,
                    reviewer=reviewer,  # type: ignore[arg-type]
                    risk_level=item.risk_level,
                    issue=item.issue,
                    suggestion=item.suggestion,
                    original_quote=item.original_quote,
                    law_name=item.law_name,
                    article_no=item.article_no,
                    cited_quote=cited_quote,
                    rule_id=item.rule_id,
                    risk_type=item.risk_type or "other",
                    reviewers=[reviewer],
                    citation_reason=item.citation_reason,
                    engine=provider.engine,
                    doc_id=clause.doc_id,
                    locator=clause.locator,
                )
            )

        if provider.engine == "api" and not payload.findings:
            # mock 的“无风险”是正常结果；真实 Provider 返回空 findings 属于异常空输出，归档但不阻断流程。
            reason = f"{clause.clause_id}: 模型返回 findings=[]"
            if failure_log is not None:
                failure_log.record(
                    matter_id=matter_id,
                    node=f"review_{reviewer}",
                    reason=reason,
                    raw_output_hash=hash_payload(raw_payload),
                    retriable=True,
                    category="empty_output",
                )
            warnings.append(f"{clause.clause_id} {reviewer} 空输出[empty_output]")
            log_event(
                logger,
                "review.empty",
                matter_id=matter_id,
                node=f"review_{reviewer}",
                model=provider.model,
                tokens=pt + ct,
                latency=int((time.perf_counter() - clause_start) * 1000),
                status="empty",
                device=device,
                clause_id=clause.clause_id,
                reviewer=reviewer,
                category="empty_output",
            )

        log_event(
            logger,
            "review.done",
            matter_id=matter_id,
            node=f"review_{reviewer}",
            model=provider.model,
            tokens=pt + ct,
            latency=int((time.perf_counter() - clause_start) * 1000),
            status="ok" if payload.findings else "empty",
            device=device,
            clause_id=clause.clause_id,
            reviewer=reviewer,
            findings=len(payload.findings),
        )

    return findings, warnings, prompt_tokens, completion_tokens
