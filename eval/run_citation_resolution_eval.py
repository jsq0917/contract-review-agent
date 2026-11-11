"""引用补证离线对照（不调用付费 API）。

做法：用确定性 mock Provider 生成 findings，再包一层「引用盲」——抹掉
law_name/article_no/cited_quote/citation_reason/rule_id，模拟真实 API 未给引用
的情形。然后分别关闭 / 开启 resolve_citations，比较补证前后的引用覆盖率、
工具步数与耗时。

**不能证明的事**：mock 的 token 计数恒为 0、成本为 null，因此本脚本无法给出
真实 API 的 per-finding token 与费用；这两项需要付费 API，未执行。
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import lexflow.service as service_module
from lexflow.config import Settings
from lexflow.llm.mock import MockProvider
from lexflow.service import AnalyzeService

MANIFEST = ROOT / "eval" / "public_templates" / "manifest.json"
OUT_JSON = ROOT / "eval" / "citation_resolution_results.json"
OUT_MD = ROOT / "eval" / "citation_resolution_results.md"

STRIP_FIELDS = ("law_name", "article_no", "cited_quote", "citation_reason", "rule_id")


class CitationBlindProvider:
    """对 mock 输出做确定性脱引用；engine 标为 api，避免 reviewer 用规则表补回。"""

    engine = "api"

    def __init__(self, inner: MockProvider) -> None:
        self.inner = inner
        self.model = "mock-deterministic-v2(citation-blind)"

    def complete_json(self, system, user, schema_hint=None):
        return self.inner.complete_json(system, user, schema_hint)

    def complete_json_with_meta(self, system, user, schema_hint=None, *, validator=None, prompt_version=""):
        result = self.inner.complete_json_with_meta(
            system, user, schema_hint, validator=None, prompt_version=prompt_version
        )
        payload = json.loads(json.dumps(result.payload))
        for item in payload.get("findings", []):
            for field in STRIP_FIELDS:
                item[field] = None
        result.payload = payload
        return result

    def complete_with_tools(self, messages, tools, *, tool_choice="auto", prompt_version=""):
        return self.inner.complete_with_tools(
            messages, tools, tool_choice=tool_choice, prompt_version=prompt_version
        )


def _run(settings: Settings, path: Path) -> dict:
    service = AnalyzeService(settings)
    result = service.analyze(
        path,
        stance="甲方",
        as_of=None,
        engine="api",
        require_confirmation=False,
        auto_approve=True,
    )
    report = result.report
    findings = report.get("findings", [])
    cited = sum(1 for item in findings if item.get("law_name") and item.get("article_no"))
    resolutions = result.raw_state.get("citation_resolutions", []) or []
    llm_meta = result.raw_state.get("llm_calls_meta", []) or []
    resolve_calls = [
        item for item in llm_meta if item.get("node") == "resolve_citations"
    ]
    per_finding = []
    for item in resolutions:
        calls = [
            call for call in resolve_calls if call.get("finding_id") == item.get("finding_id")
        ]
        per_finding.append(
            {
                "finding_id": item.get("finding_id"),
                "clause_id": item.get("clause_id"),
                "reviewer": item.get("reviewer"),
                "status": item.get("status"),
                "tool_calls": item.get("tool_calls", 0),
                "llm_calls": len(calls),
                "prompt_tokens": sum(int(call.get("prompt_tokens") or 0) for call in calls),
                "completion_tokens": sum(int(call.get("completion_tokens") or 0) for call in calls),
                "elapsed_ms": item.get("elapsed_ms", 0),
                "law_name": item.get("law_name"),
                "article_no": item.get("article_no"),
                "reason": item.get("reason", ""),
            }
        )
    metrics = report.get("metrics") or {}
    return {
        "matter_id": result.matter_id,
        "trace_id": result.trace_id,
        "findings": len(findings),
        "cited": cited,
        "uncited": len(findings) - cited,
        "citation_coverage": round(cited / len(findings), 4) if findings else None,
        "resolved": sum(1 for item in resolutions if item.get("status") == "resolved"),
        "refused": sum(1 for item in resolutions if item.get("status") == "no_applicable_law"),
        "skipped_budget": sum(
            1 for item in resolutions if item.get("status") == "skipped_budget"
        ),
        "failed": sum(
            1 for item in resolutions if item.get("status") in {"error", "partial"}
        ),
        "tool_calls": len(result.raw_state.get("tool_calls", []) or []),
        "resolve_llm_calls": len(resolve_calls),
        "prompt_tokens": metrics.get("prompt_tokens", 0),
        "completion_tokens": metrics.get("completion_tokens", 0),
        "cost_estimate_cny": metrics.get("cost_estimate_cny"),
        "resolve_latency_ms": (metrics.get("node_latency_ms") or {}).get("resolve_citations"),
        "total_elapsed_ms": result.elapsed_ms,
        "per_finding": per_finding,
    }


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    base = replace(
        Settings(),
        embedding_enabled=False,
        reranker_enabled=False,
    )
    original = service_module.build_provider
    service_module.build_provider = lambda settings, engine: CitationBlindProvider(
        MockProvider()
    )
    rows: list[dict] = []
    try:
        for case in manifest["cases"]:
            path = ROOT / case["path"]
            for label, enabled in (("off", False), ("on", True)):
                settings = replace(base, resolve_citations=enabled)
                outcome = _run(settings, path)
                rows.append({"case": case["id"], "mode": label, **outcome})
    finally:
        service_module.build_provider = original

    OUT_JSON.write_text(
        json.dumps(
            {
                "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                "engine": "mock + citation-blind wrapper（无付费调用）",
                "note": "mock token 恒为 0、成本为 null，本文件不能证明真实 API 的 token/费用。",
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = [
        "# 引用补证离线对照（mock + citation-blind，无付费调用）",
        "",
        "> 目的：验证 `resolve_citations` 在「上游完全没给引用」时能补回多少、花多少步数。",
        "> 限制：mock Provider 的 token 恒为 0、成本为 null；**per-finding token 与费用无法由本脚本证明**，",
        "> 需要真实 API 才能测量（未执行，未获批准）。",
        "",
        "| case | 模式 | findings | 引用/未引用 | 引用覆盖率 | 补证 resolved/refused | 工具步数 | 补证耗时ms | 总耗时ms |",
        "|---|---|---:|---|---:|---|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['case']} | {row['mode']} | {row['findings']} | "
            f"{row['cited']}/{row['uncited']} | {row['citation_coverage']} | "
            f"{row['resolved']}/{row['refused']} | {row['tool_calls']} | "
            f"{row['resolve_latency_ms']} | {row['total_elapsed_ms']} |"
        )

    lines += [
        "",
        "## 逐条补证明细（mode=on）",
        "",
        "| case | finding | clause | 审查路 | 状态 | 工具步数 | 模型调用 | prompt tokens | completion tokens | 耗时ms | 法条 |",
        "|---|---|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        if row["mode"] != "on":
            continue
        for item in row["per_finding"]:
            lines.append(
                f"| {row['case']} | {item['finding_id']} | {item['clause_id']} | "
                f"{item['reviewer']} | {item['status']} | {item['tool_calls']} | "
                f"{item['llm_calls']} | {item['prompt_tokens']} | "
                f"{item['completion_tokens']} | {item['elapsed_ms']} | "
                f"{item['law_name'] or '—'} {item['article_no'] or ''} |"
            )

    lines += [
        "",
        "## 结论与降级",
        "",
        "- 离线可证明：补证能把「零引用」恢复到可核对引用；本轮数字见上表。",
        "- 离线不可证明：真实 API 的 per-finding token、补证带来的费用增量、真实延迟分布。",
        "  因此「补证成本可控」不作为当前可宣称能力，只说「有轮次/步数/条数上限」。",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(OUT_MD.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
