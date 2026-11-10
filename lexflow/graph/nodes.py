"""主图节点。每个节点只做一件事，并在 state 中留下可审计字段。"""

from __future__ import annotations

import time
from datetime import date
from typing import Any

from langgraph.types import interrupt

from lexflow.graph.state import merge_duplicate_findings
from lexflow.llm.base import LLMProvider
from lexflow.logging import get_logger, log_event
from lexflow.models import Clause, Evidence, Finding, HumanDecision, ParsedDocument, Report, Verification
from lexflow.parse import parse_document
from lexflow.report.docx_export import export_docx
from lexflow.review.reviewer import review_clauses
from lexflow.segment import segment_clauses
from lexflow.storage.db import Store
from lexflow.trace import Tracer
from lexflow.verify.gates import verify_finding

logger = get_logger("lexflow.graph")


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _record(tracer: Tracer | None, event: str, **fields: Any) -> None:
    if tracer is not None:
        tracer.record(event, **fields)


def parse_node(state: dict[str, Any], tracer: Tracer | None = None) -> dict[str, Any]:
    start = time.perf_counter()
    parsed = parse_document(state["doc_path"])
    latency = _ms(start)
    log_event(
        logger,
        "node.done",
        matter_id=state["matter_id"],
        node="parse",
        model="parser",
        tokens=0,
        latency=latency,
        status="ok",
        device="parser",
    )
    _record(
        tracer,
        "node.end",
        node="parse",
        latency_ms=latency,
        source_type=parsed.source_type,
        pages=len(parsed.pages),
        chars=len(parsed.full_text),
        tables=len(parsed.tables),
    )
    return {
        "parsed": parsed.model_dump(mode="json"),
        "warnings": parsed.warnings,
        "node_timings": {"parse": latency},
    }


def segment_node(state: dict[str, Any], store: Store, tracer: Tracer | None = None) -> dict[str, Any]:
    start = time.perf_counter()
    parsed = ParsedDocument(**state["parsed"])
    clauses = segment_clauses(parsed)
    store.replace_clauses(state["matter_id"], clauses)
    warnings: list[str] = []
    if not clauses:
        warnings.append("未切分出条款，请检查文档格式")
    latency = _ms(start)
    log_event(
        logger,
        "node.done",
        matter_id=state["matter_id"],
        node="segment_clauses",
        model="rule",
        tokens=0,
        latency=latency,
        status="ok",
        device="rule",
    )
    _record(
        tracer,
        "node.end",
        node="segment_clauses",
        latency_ms=latency,
        clauses=len(clauses),
    )
    return {
        "clauses": [clause.model_dump(mode="json") for clause in clauses],
        "warnings": warnings,
        "node_timings": {"segment_clauses": latency},
    }


def _review_node(
    state: dict[str, Any],
    reviewer: str,
    provider: LLMProvider,
    retriever: Any,
    failure_log: Any = None,
    top_k: int = 3,
    tracer: Tracer | None = None,
) -> dict[str, Any]:
    start = time.perf_counter()
    clauses = [Clause(**item) for item in state.get("clauses", [])]
    call_meta: list[dict[str, Any]] = []
    findings, warnings, prompt_tokens, completion_tokens = review_clauses(
        clauses,
        retriever,
        provider,
        reviewer,
        matter_id=state.get("matter_id", ""),
        failure_log=failure_log,
        stance=state.get("stance", "甲方"),
        top_k=top_k,
        rerank=bool(state.get("rerank", False)),
        call_meta=call_meta,
    )
    latency = _ms(start)
    for item in call_meta:
        _record(tracer, "llm.call", node=f"review_{reviewer}", **item)
    log_event(
        logger,
        "node.done",
        matter_id=state["matter_id"],
        node=f"review_{reviewer}",
        model=provider.model,
        tokens=prompt_tokens + completion_tokens,
        latency=latency,
        status="ok",
        device=getattr(getattr(retriever, "embedder", None), "resolved_device", None) or "n/a",
    )
    _record(
        tracer,
        "node.end",
        node=f"review_{reviewer}",
        latency_ms=latency,
        clauses=len(clauses),
        findings=len(findings),
        llm_calls=len(call_meta),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    return {
        "findings": findings,
        "warnings": warnings,
        "llm_calls": len(clauses),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "llm_calls_meta": call_meta,
        "node_timings": {f"review_{reviewer}": latency},
    }


def bind_evidence_node(state: dict[str, Any], retriever: Any, tracer: Tracer | None = None) -> dict[str, Any]:
    start = time.perf_counter()
    clauses = {item["clause_id"]: Clause(**item) for item in state.get("clauses", [])}
    rerank = bool(state.get("rerank", False))
    updated: list[Finding] = []
    ledger: dict[str, Evidence] = {}
    warnings: list[str] = []
    findings = merge_duplicate_findings(state.get("findings", []))

    for finding in findings:
        clause = clauses.get(finding.clause_id)
        if clause is None:
            warnings.append(f"{finding.finding_id} 找不到对应条款")
            updated.append(finding)
            continue
        query = f"{clause.text[:500]} {finding.issue}"
        hits = retriever.retrieve(query, 6, rerank=rerank)
        selected = None
        if finding.law_name and finding.article_no:
            selected = next(
                (
                    article
                    for article, score in hits
                    if article.law_name == finding.law_name and article.article_no == finding.article_no
                ),
                None,
            )
            if selected is None:
                # 引用真实存在但未进候选时，从语料直查，仍由 G1/G2/G3 核验。
                selected = next(
                    (
                        article
                        for article in retriever.articles
                        if article.law_name == finding.law_name and article.article_no == finding.article_no
                    ),
                    None,
                )
        if selected is None:
            # 未指定法条只记录原因，不拿检索首位硬凑成“依据”。
            reason = finding.citation_reason or "未指定法条"
            warnings.append(f"{finding.finding_id} 未指定法条：{reason}")
            updated.append(finding)
            continue

        key = f"{selected.law_name}#{selected.article_no}"
        evidence = ledger.get(key)
        if evidence is None:
            evidence = Evidence(
                evidence_id=f"E{len(ledger) + 1:03d}",
                law_name=selected.law_name,
                article_no=selected.article_no,
                quote=selected.text,
                effective_from=selected.effective_from,
                effective_to=selected.effective_to,
                source_url=selected.source_url,
                version_id=selected.version_id,
                score=next((score for article, score in hits if article is selected), 0.5),
            )
            ledger[key] = evidence
        updated.append(finding.model_copy(update={"evidence_id": evidence.evidence_id}))

    latency = _ms(start)
    _record(
        tracer,
        "node.end",
        node="bind_evidence",
        latency_ms=latency,
        findings=len(updated),
        evidence=len(ledger),
        uncited=sum(1 for item in updated if not (item.law_name and item.article_no)),
        retrieval_mode=retriever.mode,
    )
    log_event(
        logger,
        "node.done",
        matter_id=state["matter_id"],
        node="bind_evidence",
        model="retriever",
        tokens=0,
        latency=latency,
        status="ok",
        device=retriever.embedder.resolved_device or "n/a",
        retrieval_mode=retriever.mode,
        retrieval_device=retriever.embedder.resolved_device or "n/a",
    )
    return {
        "findings": updated,
        "evidence": list(ledger.values()),
        "warnings": warnings,
        "retrieval_mode": retriever.mode,
        "retrieval_device": retriever.embedder.resolved_device or "n/a",
        "node_timings": {"bind_evidence": latency},
    }


def verify_node(state: dict[str, Any], tracer: Tracer | None = None) -> dict[str, Any]:
    start = time.perf_counter()
    as_of = date.fromisoformat(state["as_of"])
    ledger = state.get("evidence", [])
    verifications: list[Verification] = []
    for finding in state.get("findings", []):
        verifications.extend(verify_finding(finding, ledger, as_of))
    latency = _ms(start)
    counts: dict[str, dict[str, int]] = {}
    for item in verifications:
        bucket = counts.setdefault(item.gate, {"pass": 0, "fail": 0, "skipped": 0})
        bucket[item.status] = bucket.get(item.status, 0) + 1
    _record(tracer, "node.end", node="verify", latency_ms=latency, gates=counts)
    log_event(
        logger,
        "node.done",
        matter_id=state["matter_id"],
        node="verify",
        model="rules",
        tokens=0,
        latency=latency,
        status="ok",
        device="rules",
    )
    return {"verifications": verifications, "node_timings": {"verify": latency}}


def assemble_report_node(
    state: dict[str, Any], store: Store, settings: Any, tracer: Tracer | None = None
) -> dict[str, Any]:
    start = time.perf_counter()
    findings = list(state.get("findings", []))
    evidence = list(state.get("evidence", []))
    verifications = list(state.get("verifications", []))
    prompt_tokens = int(state.get("prompt_tokens", 0))
    completion_tokens = int(state.get("completion_tokens", 0))
    cost: float | None = None
    if prompt_tokens == 0 and completion_tokens == 0:
        cost = 0.0  # mock 无 token 消耗，成本为 0，不是估算
    elif settings.price_input_per_m and settings.price_output_per_m:
        cost = (
            prompt_tokens / 1_000_000 * float(settings.price_input_per_m)
            + completion_tokens / 1_000_000 * float(settings.price_output_per_m)
        )
    resolutions = list(state.get("citation_resolutions", []))
    resolution_metrics = {
        "uncited_before": sum(1 for item in resolutions if item.get("before") == "uncited"),
        "resolved": sum(1 for item in resolutions if item.get("status") == "resolved"),
        "refused": sum(1 for item in resolutions if item.get("status") == "no_applicable_law"),
        "tool_calls": len(state.get("tool_calls", [])),
        "llm_calls": sum(
            1
            for item in state.get("llm_calls_meta", [])
            if item.get("node") == "resolve_citations"
        ),
    }
    metrics = {
        "trace_id": state.get("trace_id", ""),
        "model": state.get("model_name", ""),
        "prompt_version": state.get("prompt_version", ""),
        "corpus_version": state.get("corpus_version", ""),
        "channels": list(state.get("channels", ["legal", "commercial"])),
        "llm_calls": int(state.get("llm_calls", 0)),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_estimate_cny": cost,
        "node_latency_ms": dict(state.get("node_timings", {})),
        "retrieval_mode": state.get("retrieval_mode", "bm25-only"),
        "retrieval_device": state.get("retrieval_device", "n/a"),
        "citation_resolution": resolution_metrics,
    }
    report = Report(
        matter_id=state["matter_id"],
        engine=state.get("engine", "mock"),
        stance=state.get("stance", "neutral"),
        as_of=date.fromisoformat(state["as_of"]),
        doc_id=state.get("parsed", {}).get("doc_id", ""),
        findings=findings,
        evidence=evidence,
        verifications=verifications,
        warnings=list(state.get("warnings", [])),
        metrics=metrics,
    )
    store.replace_findings(state["matter_id"], findings)
    store.replace_evidence(state["matter_id"], evidence)
    store.replace_verifications(state["matter_id"], verifications)
    store.update_matter(state["matter_id"], status="awaiting_review", result=report)
    latency = _ms(start)
    _record(
        tracer,
        "node.end",
        node="assemble_report",
        latency_ms=latency,
        findings=len(findings),
        evidence=len(evidence),
        verifications=len(verifications),
        warnings=len(report.warnings),
    )
    log_event(
        logger,
        "node.done",
        matter_id=state["matter_id"],
        node="assemble_report",
        model="template",
        tokens=0,
        latency=latency,
        status="ok",
        device="template",
    )
    return {
        "report": report.model_dump(mode="json"),
        "node_timings": {"assemble_report": latency},
    }


def export_docx_node(
    state: dict[str, Any],
    store: Store,
    template_path: str,
    artifacts_dir: str,
    tracer: Tracer | None = None,
) -> dict[str, Any]:
    start = time.perf_counter()
    report = Report(**state["report"])
    decision: HumanDecision | None = None
    if state.get("require_confirmation", True) and not state.get("auto_approve", False):
        payload = {
            "matter_id": state["matter_id"],
            "engine": state.get("engine", "mock"),
            "findings_count": len(report.findings),
            "message": "请确认风险清单后再导出 docx；mock 结果不构成法律意见。",
        }
        resumed = interrupt(payload)
        if isinstance(resumed, dict):
            decision = HumanDecision(
                action=resumed.get("action", "approve"),
                comment=str(resumed.get("comment", "")),
                decided_at=date.today().isoformat(),
            )
        else:
            decision = HumanDecision(action="approve", comment="", decided_at=date.today().isoformat())
    if decision is not None:
        store.add_human_review(
            state["matter_id"], decision.action, decision.comment, decision.model_dump(mode="json")
        )

    if decision is not None and decision.action == "reject":
        store.update_matter(state["matter_id"], status="rejected")
        latency = _ms(start)
        _record(
            tracer,
            "hitl.decision",
            node="export_docx",
            action=decision.action,
            latency_ms=latency,
            exported=False,
        )
        log_event(
            logger,
            "node.done",
            matter_id=state["matter_id"],
            node="export_docx",
            model="docxtpl",
            tokens=0,
            latency=latency,
            status="rejected",
            device="docxtpl",
        )
        return {
            "docx_path": "",
            "human_decision": decision.model_dump(mode="json"),
            "node_timings": {"export_docx": latency},
        }

    from pathlib import Path

    output = Path(artifacts_dir) / state["matter_id"] / "report.docx"
    export_docx(report, template_path, output)
    store.update_matter(state["matter_id"], status="completed", docx_path=str(output))
    latency = _ms(start)
    _record(
        tracer,
        "hitl.decision",
        node="export_docx",
        action=decision.action if decision else "auto_approve",
        latency_ms=latency,
        exported=True,
        output=output.name,
    )
    log_event(
        logger,
        "node.done",
        matter_id=state["matter_id"],
        node="export_docx",
        model="docxtpl",
        tokens=0,
        latency=latency,
        status="ok",
        device="docxtpl",
    )
    return {
        "docx_path": str(output),
        "human_decision": decision.model_dump(mode="json") if decision else {},
        "node_timings": {"export_docx": latency},
    }
