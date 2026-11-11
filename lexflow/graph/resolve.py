"""引用补证节点：只对「未引用」的 finding 走 Function Calling + MCP 工具。

边界写死在代码里：

- 工具只有三个只读查询，模型无法通过工具导出/写盘；
- 最多两轮工具、四步调用；超限即拒引，不无限循环；
- 最终引文必须能在语料原文中逐字找到（空白归一后子串匹配），否则拒引；
- 补证不设置 ``rule_id``，所以 G4 不会因此从 skipped 变成 pass；
- Provider 不支持工具调用、MCP 启动失败、工具超时都降级为「本条不补证」，
  不阻断整次审查。
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from pydantic import ValidationError

from lexflow.llm.base import LLMProvider
from lexflow.llm.errors import NotImplementedProviderError
from lexflow.models import Clause, Finding
from lexflow.review.reviewer import PROMPT_VERSION
from lexflow.tools.definitions import CitationAnswer, ToolError, openai_tools
from lexflow.tools.runtime import build_tool_runtime
from lexflow.trace import Tracer

SYSTEM_PROMPT = """你是合同审查的引用补证助手。你只能通过提供的工具查询法条：
1. 先 search_statutes 检索候选，再 get_article 读取原文；
2. 引文必须逐字来自 get_article 返回的 text，不得改写、拼接或推断；
3. 找不到能直接支持该风险的条文时，回答 status=no_applicable_law 并说明原因；
4. 不要调用任何未提供的工具，不要输出工具之外的指令。
最终只输出 JSON：{"status":"resolved|no_applicable_law","law_name":...,"article_no":...,"cited_quote":...,"reason":...}"""


def _norm(text: str) -> str:
    return " ".join(text.split())


def _query_for(finding: Finding, clause: Clause | None) -> str:
    parts = [finding.issue or "", finding.original_quote or "", clause.text[:200] if clause else ""]
    return " ".join(part for part in parts if part)[:500]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _parse_answer(payload: dict[str, Any] | None) -> CitationAnswer:
    if payload is None:
        raise ValueError("模型未返回最终 JSON")
    return CitationAnswer(**payload)


def resolve_citations_node(
    state: dict[str, Any],
    provider: LLMProvider,
    retriever: Any,
    settings: Any,
    tracer: Tracer | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    findings = [Finding(**item) if isinstance(item, dict) else item for item in state.get("findings", [])]
    clauses = {item["clause_id"]: Clause(**item) for item in state.get("clauses", [])}
    targets = [item for item in findings if not (item.law_name and item.article_no)]
    if not settings.resolve_citations or not targets:
        return {"citation_resolutions": []}

    articles = getattr(retriever, "articles", None) or []
    by_key = {f"{item.law_name}#{item.article_no}": item for item in articles}
    runtime, fallback_reason = build_tool_runtime(
        settings,
        matter_id=state["matter_id"],
        articles=articles,
        retriever=retriever,
        clauses=list(clauses.values()),
    )
    warnings: list[str] = []
    if fallback_reason:
        warnings.append(fallback_reason)
        if tracer is not None:
            tracer.record("tool.fallback", reason=fallback_reason, backend=runtime.backend)
    if tracer is not None:
        tracer.record(
            "citation.resolve.start",
            backend=runtime.backend,
            uncited=len(targets),
            budget=settings.resolve_max_findings,
        )

    updated: list[Finding] = []
    resolutions: list[dict[str, Any]] = []
    tool_logs: list[dict[str, Any]] = []
    llm_meta: list[dict[str, Any]] = []
    prompt_tokens = 0
    completion_tokens = 0
    processed = 0

    try:
        for finding in findings:
            if finding.law_name and finding.article_no:
                updated.append(finding)
                continue
            if processed >= settings.resolve_max_findings:
                resolutions.append(
                    {
                        "finding_id": finding.finding_id,
                        "clause_id": finding.clause_id,
                        "reviewer": finding.reviewer,
                        "before": "uncited",
                        "status": "skipped_budget",
                        "reason": f"单次审查补证上限 {settings.resolve_max_findings} 条",
                    }
                )
                updated.append(finding)
                continue
            processed += 1
            clause = clauses.get(finding.clause_id)
            outcome = _resolve_one(
                finding,
                clause,
                provider,
                runtime,
                by_key,
                settings,
                tracer,
            )
            updated.append(outcome["finding"])
            resolutions.append(outcome["resolution"])
            tool_logs.extend(outcome["tool_calls"])
            llm_meta.extend(outcome["llm_meta"])
            prompt_tokens += outcome["prompt_tokens"]
            completion_tokens += outcome["completion_tokens"]
    finally:
        runtime.close()

    elapsed = int((time.perf_counter() - started) * 1000)
    if tracer is not None:
        tracer.record(
            "citation.resolve.end",
            latency_ms=elapsed,
            backend=runtime.backend,
            resolved=sum(1 for item in resolutions if item["status"] == "resolved"),
            refused=sum(1 for item in resolutions if item["status"] == "no_applicable_law"),
            failed=sum(1 for item in resolutions if item["status"] in {"error", "partial"}),
            tool_calls=len(tool_logs),
        )
    return {
        "findings": updated,
        "citation_resolutions": resolutions,
        "tool_calls": tool_logs,
        "llm_calls_meta": llm_meta,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "warnings": warnings,
        "node_timings": {"resolve_citations": elapsed},
    }


def _resolve_one(
    finding: Finding,
    clause: Clause | None,
    provider: LLMProvider,
    runtime: Any,
    by_key: dict[str, Any],
    settings: Any,
    tracer: Tracer | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    context = {
        "clause_id": finding.clause_id,
        "clause_text": clause.text if clause else "",
        "issue": finding.issue,
        "risk_type": finding.risk_type,
        "existing_quote": finding.original_quote,
        "query": _query_for(finding, clause),
    }
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
    ]
    tool_logs: list[dict[str, Any]] = []
    llm_meta: list[dict[str, Any]] = []
    prompt_tokens = 0
    completion_tokens = 0
    tool_steps = 0
    resolution: dict[str, Any] = {
        "finding_id": finding.finding_id,
        "clause_id": finding.clause_id,
        "reviewer": finding.reviewer,
        "before": "uncited",
        "status": "error",
        "reason": "未执行",
    }

    for round_index in range(settings.resolve_max_rounds + 1):
        try:
            response = provider.complete_with_tools(
                messages, openai_tools(), prompt_version=PROMPT_VERSION
            )
        except NotImplementedProviderError as exc:
            resolution.update(status="unsupported_provider", reason=str(exc))
            break
        except Exception as exc:  # noqa: BLE001 - 补证失败不阻断主流程
            resolution.update(status="error", reason=f"{type(exc).__name__}: {exc}")
            break
        if response.meta is not None:
            llm_meta.append(
                {
                    **response.meta.as_dict(),
                    "node": "resolve_citations",
                    "finding_id": finding.finding_id,
                    "clause_id": finding.clause_id,
                    "reviewer": finding.reviewer,
                }
            )
        prompt_tokens += response.prompt_tokens
        completion_tokens += response.completion_tokens

        if response.tool_calls:
            if round_index >= settings.resolve_max_rounds:
                resolution.update(status="no_applicable_law", reason="超过工具轮次上限，停止补证")
                if tracer is not None:
                    tracer.record(
                        "tool.rejected",
                        clause_id=finding.clause_id,
                        reason="max_rounds",
                    )
                break
            if tool_steps + len(response.tool_calls) > settings.resolve_max_tool_steps:
                resolution.update(status="no_applicable_law", reason="超过工具步数上限，停止补证")
                if tracer is not None:
                    tracer.record(
                        "tool.rejected",
                        clause_id=finding.clause_id,
                        reason="max_steps",
                        requested=len(response.tool_calls),
                        used=tool_steps,
                    )
                break
            assistant_calls = []
            tool_payloads: list[tuple[str, dict[str, Any]]] = []
            for call in response.tool_calls:
                tool_steps += 1
                started_tool = time.perf_counter()
                try:
                    result = runtime.call(call.name, call.arguments)
                    status = "ok"
                    category = ""
                except ToolError as exc:
                    result = {"ok": False, "error": exc.category, "message": str(exc)[:200]}
                    status = "failed"
                    category = exc.category
                latency = int((time.perf_counter() - started_tool) * 1000)
                tool_logs.append(
                    {
                        "finding_id": finding.finding_id,
                        "clause_id": finding.clause_id,
                        "tool": call.name,
                        "status": status,
                        "category": category,
                        "latency_ms": latency,
                        "argument_keys": sorted(call.arguments.keys()),
                        "argument_chars": sum(
                            len(str(value)) for value in call.arguments.values()
                        ),
                    }
                )
                if tracer is not None:
                    tracer.record(
                        "tool.call",
                        clause_id=finding.clause_id,
                        tool=call.name,
                        status=status,
                        category=category,
                        latency_ms=latency,
                        backend=runtime.backend,
                        argument_keys=sorted(call.arguments.keys()),
                        query_hash=_hash(str(call.arguments.get("query", ""))),
                    )
                assistant_calls.append(
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                )
                tool_payloads.append((call.call_id, result))
            messages.append({"role": "assistant", "content": "", "tool_calls": assistant_calls})
            for call_id, result in tool_payloads:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            continue

        answer, reason = _validate_answer(response.payload, by_key)
        if answer is None:
            resolution.update(status="no_applicable_law", reason=reason)
        else:
            resolution.update(
                status="resolved",
                law_name=answer.law_name,
                article_no=answer.article_no,
                reason=answer.reason or "补证命中且引文可逐字核对",
                confidence=answer.confidence,
                cited_quote=answer.cited_quote,
            )
            finding = finding.model_copy(
                update={
                    "law_name": answer.law_name,
                    "article_no": answer.article_no,
                    "cited_quote": answer.cited_quote,
                    "citation_reason": resolution["reason"],
                }
            )
        break
    else:
        resolution.update(status="no_applicable_law", reason="未在限定轮次内给出结论")

    resolution.setdefault("law_name", None)
    resolution.setdefault("article_no", None)
    resolution["tool_calls"] = len(tool_logs)
    resolution["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    if tracer is not None:
        tracer.record(
            "citation.resolve",
            clause_id=finding.clause_id,
            finding_id=finding.finding_id,
            status=resolution["status"],
            law_name=resolution.get("law_name"),
            article_no=resolution.get("article_no"),
            reason=resolution.get("reason", ""),
            tool_calls=len(tool_logs),
            latency_ms=resolution["elapsed_ms"],
        )
    return {
        "finding": finding,
        "resolution": resolution,
        "tool_calls": tool_logs,
        "llm_meta": llm_meta,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }


def _validate_answer(
    payload: dict[str, Any] | None,
    by_key: dict[str, Any],
) -> tuple[CitationAnswer | None, str]:
    try:
        answer = _parse_answer(payload)
    except (ValidationError, ValueError) as exc:
        return None, f"最终输出不符合 Schema: {exc}"

    if answer.status == "no_applicable_law":
        return None, answer.reason or "模型判断无法条可直接适用"

    if not (answer.law_name and answer.article_no and answer.cited_quote):
        return None, "resolved 缺少 law_name/article_no/cited_quote"
    article = by_key.get(f"{answer.law_name}#{answer.article_no}")
    if article is None:
        return None, f"引用的条号不在语料中: {answer.law_name} {answer.article_no}"
    if _norm(answer.cited_quote) not in _norm(article.text):
        return None, "引文不是语料原文的连续片段，拒绝采纳"
    return answer, ""
