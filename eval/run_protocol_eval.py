"""独立评测协议：按模板来源分 dev/test，分指标输出，自审标注单列。

本脚本只做两件事：
1. 用同一份代码跑 manifest 里的样本；
2. 把「可机械复算」的计数与比率写出来，算不出的写 null，不用 0 代替。

明确不评测：法条与条款的语义适用性（G4 目前不做语义核验）。该项在产物里
固定标成 ``applicability_evaluated=false``，并列出待人工判断的条目。

用法::

    uv run python eval/run_protocol_eval.py --engine mock
    LEXFLOW_LIVE_API=1 uv run python eval/run_protocol_eval.py --engine api

API 模式必须显式设置 ``LEXFLOW_LIVE_API=1``，避免误跑付费批量抽取。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import metrics  # noqa: E402
from lexflow.parse import parse_document  # noqa: E402
from lexflow.segment import segment_clauses  # noqa: E402
from lexflow.service import AnalyzeService  # noqa: E402

MANIFEST = ROOT / "eval" / "public_templates" / "manifest.json"
OUT_JSON = ROOT / "eval" / "protocol_results.json"
OUT_MD = ROOT / "eval" / "protocol_results.md"


def load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases") or []
    if not cases:
        raise ValueError("manifest 没有 cases")
    by_source: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        if case.get("split") not in {"dev", "test"}:
            raise ValueError(f"{case.get('id')}: split 必须是 dev/test")
        by_source[case["source"]].add(case["split"])
    leaked = {source: splits for source, splits in by_source.items() if len(splits) > 1}
    if leaked:
        raise ValueError(f"同一模板来源跨 dev/test，评测泄漏: {leaked}")
    return data


def evaluate_case(service: AnalyzeService, case: dict[str, Any], engine: str) -> dict[str, Any]:
    sample = ROOT / case["path"]
    result = service.analyze(
        sample,
        stance=case.get("stance", "甲方"),
        as_of=case.get("as_of"),
        engine=engine,
        require_confirmation=False,
        auto_approve=True,
    )
    report = result.report
    findings = report.get("findings", [])
    verifications = report.get("verifications", [])
    clause_text = {item.clause_id: item.text for item in segment_clauses(parse_document(sample))}

    expected = [str(item) for item in case.get("expected_risk_clause_ids") or []]
    found_clauses = {str(item.get("clause_id")) for item in findings}
    matched = [item for item in expected if item in found_clauses]
    dual = metrics.dual_review_stats(findings)
    gates = metrics.gates_summary(findings, verifications)
    gate_counts: dict[str, dict[str, int]] = {}
    for gate in metrics.GATES:
        values = [
            metrics.gate_status(item) for item in verifications if item.get("gate") == gate
        ]
        gate_counts[gate] = {
            "pass": values.count("pass"),
            "fail": values.count("fail"),
            "skipped": values.count("skipped"),
        }
    metrics_block = report.get("metrics", {})
    resolution = metrics_block.get("citation_resolution", {})
    cited = sum(1 for item in findings if metrics.citation_status(item) == "cited")
    strict = sum(1 for item in findings if metrics.strict_traceable(item, clause_text))
    return {
        "id": case["id"],
        "split": case["split"],
        "source": case["source"],
        "sample": case["path"],
        "note": case.get("note", ""),
        "label_source": "self_review",
        "trace_id": result.trace_id,
        "engine": result.engine,
        "elapsed_ms": result.elapsed_ms,
        "counts": {
            "findings": len(findings),
            "risk_points": dual["risk_points"],
            "merged_both": dual["merged_both"],
            "legal_only": dual["legal_only"],
            "commercial_only": dual["commercial_only"],
            "duplicates": dual["duplicates"],
            "cited_findings": cited,
            "strict_traceable": strict,
            "uncited_findings": len(findings) - cited,
            "expected_risk_clauses": len(expected),
            "matched_risk_clauses": len(matched),
        },
        "ratios": {
            "strict_traceable_rate": _ratio(strict, len(findings)),
            "citation_coverage": _ratio(cited, len(findings)),
            "risk_clause_recall": _ratio(len(matched), len(expected)) if expected else None,
        },
        "gates": gate_counts,
        "gates_summary": gates,
        "citation_resolution": resolution,
        "tokens": {
            "prompt": metrics_block.get("prompt_tokens"),
            "completion": metrics_block.get("completion_tokens"),
        },
        "cost_estimate_cny": metrics_block.get("cost_estimate_cny"),
        "applicability_evaluated": False,
        "uncertain_items": case.get("uncertain_items") or [],
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_split[row["split"]].append(row)
    summary: dict[str, Any] = {}
    for split, items in by_split.items():
        findings = sum(item["counts"]["findings"] for item in items)
        risk_points = sum(item["counts"]["risk_points"] for item in items)
        cited = sum(item["counts"]["cited_findings"] for item in items)
        strict = sum(item["counts"]["strict_traceable"] for item in items)
        expected = sum(item["counts"]["expected_risk_clauses"] for item in items)
        matched = sum(item["counts"]["matched_risk_clauses"] for item in items)
        summary[split] = {
            "cases": len(items),
            "templates": sorted({item["source"] for item in items}),
            "findings": findings,
            "risk_points": risk_points,
            "merged_both": sum(item["counts"]["merged_both"] for item in items),
            "legal_only": sum(item["counts"]["legal_only"] for item in items),
            "commercial_only": sum(item["counts"]["commercial_only"] for item in items),
            "duplicates": sum(item["counts"]["duplicates"] for item in items),
            "strict_traceable_rate": _ratio(strict, findings),
            "citation_coverage": _ratio(cited, findings),
            "risk_clause_recall": _ratio(matched, expected) if expected else None,
            "uncited_findings": findings - cited,
            "g4_skipped": sum(item["gates"]["G4"]["skipped"] for item in items),
            "applicability_evaluated": False,
            "avg_elapsed_ms": round(sum(item["elapsed_ms"] for item in items) / len(items), 1),
            "all_pass_strict": sum(item["gates_summary"]["all_pass"] for item in items),
            "total_cost_estimate_cny": None
            if any(item["cost_estimate_cny"] is None for item in items)
            else round(sum(item["cost_estimate_cny"] or 0 for item in items), 6),
        }
    return summary


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 独立评测协议结果",
        "",
        f"- 生成时间：{payload['generated_at']}",
        f"- 引擎：`{payload['engine']}`",
        f"- 标注来源：`{payload['label_source']}`（自审，非律师标注）",
        "- **语义适用性（法条与条款是否真正相关）未评测**：G4 只做规则/存在性校验，"
        "产物中 `applicability_evaluated=false`。",
        "",
        "## 分集汇总",
        "",
        "| split | 模板数 | 模板来源 | findings | 风险点 | 两路共有 | 法务独有 | 商务独有 | 严格溯源率 | 引用覆盖率 | 条款级召回 | G4 skipped | 平均耗时(ms) | 成本(CNY) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for split, row in payload["splits"].items():
        lines.append(
            "| {split} | {cases} | {templates} | {findings} | {risk_points} | {both} | {legal} | {commercial} | {strict} | {cov} | {recall} | {g4} | {latency} | {cost} |".format(
                split=split,
                cases=len(row["templates"]),
                templates=", ".join(row["templates"]),
                findings=row["findings"],
                risk_points=row["risk_points"],
                both=row["merged_both"],
                legal=row["legal_only"],
                commercial=row["commercial_only"],
                strict=_fmt(row["strict_traceable_rate"]),
                cov=_fmt(row["citation_coverage"]),
                recall=_fmt(row["risk_clause_recall"]),
                g4=row["g4_skipped"],
                latency=row["avg_elapsed_ms"],
                cost="null" if row["total_cost_estimate_cny"] is None else row["total_cost_estimate_cny"],
            )
        )
    lines += [
        "",
        "## 逐样本",
        "",
        "| id | split | findings | 风险点 | 法务独有 | 商务独有 | 严格溯源 | 引用覆盖 | 召回 | 耗时(ms) | trace_id |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in payload["cases"]:
        counts = row["counts"]
        lines.append(
            "| {id} | {split} | {findings} | {risk} | {legal} | {commercial} | {strict} | {cov} | {recall} | {elapsed} | `{trace}` |".format(
                id=row["id"],
                split=row["split"],
                findings=counts["findings"],
                risk=counts["risk_points"],
                legal=counts["legal_only"],
                commercial=counts["commercial_only"],
                strict=_fmt(row["ratios"]["strict_traceable_rate"]),
                cov=_fmt(row["ratios"]["citation_coverage"]),
                recall=_fmt(row["ratios"]["risk_clause_recall"]),
                elapsed=row["elapsed_ms"],
                trace=row["trace_id"],
            )
        )
    lines += [
        "",
        "## 限制",
        "",
        f"- {payload['note']}",
        "- 召回只在有自审标注的 dev 样本上计算；公开测试模板没有律师标注，召回记为 null。",
        "- 引用覆盖只回答「有没有引用」，不回答「引用对不对」；后者需要人工或已实现的语义核验。",
        "",
        "## 待人工判断的语义条目",
        "",
    ]
    uncertain = [item for row in payload["cases"] for item in row["uncertain_items"]]
    if not uncertain:
        lines.append("- 无")
    for item in uncertain:
        lines.append(
            f"- `{item.get('clause_id') or '-'}`：{item.get('question')}（{item.get('why_uncertain')}）"
        )
    lines.append("")
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return "null" if value is None else f"{value:.2%}"


CASES: list[dict[str, Any]] = []


def main(argv: list[str] | None = None) -> int:
    global CASES
    parser = argparse.ArgumentParser(description="lexflow 独立评测协议")
    parser.add_argument("--engine", default="mock", choices=["mock", "api"])
    parser.add_argument("--manifest", default=str(MANIFEST))
    parser.add_argument("--out-json", default=str(OUT_JSON))
    parser.add_argument("--out-md", default=str(OUT_MD))
    args = parser.parse_args(argv)
    if args.engine == "api" and os.getenv("LEXFLOW_LIVE_API") != "1":
        print("拒绝执行：API 模式需显式设置 LEXFLOW_LIVE_API=1（避免误跑付费批量抽取）")
        return 2

    manifest = load_manifest(Path(args.manifest))
    CASES = manifest["cases"]
    service = AnalyzeService()
    rows = [evaluate_case(service, case, args.engine) for case in CASES]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": args.engine,
        "label_source": manifest.get("label_source", "self_review"),
        "note": manifest.get("note", ""),
        "cases": rows,
        "splits": aggregate(rows),
    }
    Path(args.out_json).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    Path(args.out_md).write_text(render_markdown(payload), encoding="utf-8")
    for split, row in payload["splits"].items():
        print(
            f"{split}: cases={row['cases']} findings={row['findings']} "
            f"risk_points={row['risk_points']} citation_coverage={row['citation_coverage']} "
            f"recall={row['risk_clause_recall']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
