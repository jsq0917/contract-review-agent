"""M0 评测：只输出原始计数与比率，不做目标值自评达标。

没有 gold 时输出“无标注数据，指标不可计算”，不以 0 代替。
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from docx import Document

import metrics
from lexflow.service import AnalyzeService

GOLD = ROOT / "eval" / "gold.jsonl"
OUT_CSV = ROOT / "eval" / "results.csv"
OUT_MD = ROOT / "eval" / "results.md"
OUT_CSV_API = ROOT / "eval" / "results_api.csv"
OUT_MD_API = ROOT / "eval" / "results_api.md"


def compute_entry(service: AnalyzeService, row: dict, engine: str = "mock") -> dict:
    result = service.analyze(
        ROOT / row["sample"],
        stance=row.get("stance", "甲方"),
        as_of=row.get("as_of"),
        engine=engine,
        require_confirmation=False,
        auto_approve=True,
    )
    report = result.report
    findings = report.get("findings", [])
    clauses = report.get("clauses", [])
    expected = set(row["expected_risk_clause_ids"])
    found_clauses = {item["clause_id"] for item in findings}
    matched = expected & found_clauses

    # 从头读取 clauses（report 中未包含时从 DB/解析结果补）
    from lexflow.parse import parse_document
    from lexflow.segment import segment_clauses

    parsed = parse_document(ROOT / row["sample"])
    parsed_clauses = segment_clauses(parsed)
    clause_text = {item.clause_id: item.text for item in parsed_clauses}
    locatable = sum(
        1
        for finding in findings
        if finding.get("clause_id")
        and finding.get("original_quote")
        and clause_text.get(finding["clause_id"]) is not None
    )
    strict_traceable = sum(1 for finding in findings if metrics.strict_traceable(finding, clause_text))
    verifications = report.get("verifications", [])
    gate_counts = {
        gate: {
            "pass": sum(
                1
                for item in verifications
                if item["gate"] == gate and metrics.gate_status(item) == "pass"
            ),
            "total": sum(1 for item in verifications if item["gate"] == gate),
        }
        for gate in metrics.GATES
    }
    gates = metrics.gates_summary(findings, verifications)
    dual = metrics.dual_review_stats(findings)
    docx_path = Path(result.docx_path or "")
    docx_rows = 0
    if docx_path.exists():
        docx_rows = len(Document(docx_path).tables[0].rows) - 1
    note = (
        "mock 引擎，仅验证链路；比率不代表真实模型效果"
        if result.engine == "mock"
        else "真实 API 单次运行，样本量小，不可外推"
    )
    return {
        "sample": row["sample"],
        "engine": result.engine,
        "matter_id": result.matter_id,
        "trace_id": result.trace_id,
        "expected_clauses": row["expected_clause_count"],
        "parsed_clauses": len(parsed_clauses),
        "expected_risk_clauses": len(expected),
        "matched_risk_clauses": len(matched),
        "risk_clause_recall": round(len(matched) / len(expected), 4) if expected else None,
        "findings": len(findings),
        "traceable_findings": strict_traceable,
        "traceability_rate": round(strict_traceable / len(findings), 4) if findings else None,
        "locatable_findings": locatable,
        "cited_findings": gates["cited"],
        "uncited_findings": gates["uncited"],
        "findings_all_gates_pass": gates["all_pass"],
        "findings_all_gates_pass_rate": round(gates["all_pass"] / len(findings), 4) if findings else None,
        "findings_all_gates_pass_cited": gates["all_pass_cited"],
        "gates_skipped_findings": gates["skipped_findings"],
        "risk_points": dual["risk_points"],
        "merged_both": dual["merged_both"],
        "legal_only": dual["legal_only"],
        "commercial_only": dual["commercial_only"],
        "duplicate_findings": dual["duplicates"],
        "G1_pass": gate_counts["G1"]["pass"],
        "G1_total": gate_counts["G1"]["total"],
        "G2_pass": gate_counts["G2"]["pass"],
        "G2_total": gate_counts["G2"]["total"],
        "G3_pass": gate_counts["G3"]["pass"],
        "G3_total": gate_counts["G3"]["total"],
        "G4_pass": gate_counts["G4"]["pass"],
        "G4_total": gate_counts["G4"]["total"],
        "G5_pass": gate_counts["G5"]["pass"],
        "G5_total": gate_counts["G5"]["total"],
        "G5_skipped": sum(
            1
            for item in verifications
            if item["gate"] == "G5" and metrics.gate_status(item) == "skipped"
        ),
        "llm_calls": result.raw_state.get("llm_calls", 0),
        "prompt_tokens": result.raw_state.get("prompt_tokens", 0),
        "completion_tokens": result.raw_state.get("completion_tokens", 0),
        "cost_estimate_cny": (report.get("metrics") or {}).get("cost_estimate_cny"),
        "elapsed_ms": result.elapsed_ms,
        "docx_exists": docx_path.exists(),
        "docx_table_rows": docx_rows,
        "db_findings": service.store.count_findings(result.matter_id),
        "note": note,
    }


def main() -> int:
    if not GOLD.exists() or not GOLD.read_text(encoding="utf-8").strip():
        message = "无标注数据，指标不可计算"
        print(message)
        OUT_MD.write_text(f"# M0 评测\n\n{message}\n", encoding="utf-8")
        return 0
    rows = [json.loads(line) for line in GOLD.read_text(encoding="utf-8").splitlines() if line.strip()]
    import argparse

    parser = argparse.ArgumentParser(description="示例合同评测")
    parser.add_argument("--engine", default="mock", choices=["mock", "api"], help="审查引擎")
    args = parser.parse_args()

    service = AnalyzeService()
    results = [compute_entry(service, row, args.engine) for row in rows]

    out_csv = OUT_CSV_API if args.engine == "api" else OUT_CSV
    out_md = OUT_MD_API if args.engine == "api" else OUT_MD
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    lines = [f"# 示例合同评测（engine={args.engine}）（原始计数与比率）", "", f"> 引擎为 {args.engine}；以下数字只描述本次运行。" + ("mock 下仅代表链路跑通。" if args.engine == "mock" else "API 下为真实模型单次运行，样本量 2，不可外推。"), ""]
    lines.append("| 样本 | 条款数 | 风险条款召回 | 风险项 | 严格溯源率 | 引用/未引用 | 五闸全过（有引用） | 双路：风险点/两路共同/法务独有/商务独有 | 调用 | tokens(in/out) | 成本CNY | 耗时ms | docx行数 | DB条数 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for item in results:
        lines.append(
            f"| {Path(item['sample']).name} | {item['parsed_clauses']}/{item['expected_clauses']} | "
            f"{item['matched_risk_clauses']}/{item['expected_risk_clauses']} ({item['risk_clause_recall']}) | "
            f"{item['findings']} | {item['traceable_findings']}/{item['findings']} ({item['traceability_rate']}) | "
            f"{item['cited_findings']}/{item['uncited_findings']} | "
            f"{item['findings_all_gates_pass']}/{item['findings']} ({item['findings_all_gates_pass_cited']} 条有引用) | "
            f"{item['risk_points']}/{item['merged_both']}/{item['legal_only']}/{item['commercial_only']} | "
            f"{item['llm_calls']} | {item['prompt_tokens']}/{item['completion_tokens']} | "
            f"{item['cost_estimate_cny']} | {item['elapsed_ms']} | "
            f"{item['docx_table_rows']} | {item['db_findings']} |"
        )
    lines.append("")
    lines.append("> 严格溯源 = `original_quote` 归一化后是所属条款正文的子串；五闸全过要求五道闸都是 pass，skipped 不算通过。")
    lines.append("")
    lines.append("## 分闸门计数")
    for item in results:
        lines.append(
            f"- {Path(item['sample']).name}: G1 {item['G1_pass']}/{item['G1_total']}；"
            f"G2 {item['G2_pass']}/{item['G2_total']}；G3 {item['G3_pass']}/{item['G3_total']}；"
            f"G4 {item['G4_pass']}/{item['G4_total']}；G5 {item['G5_pass']}/{item['G5_total']}"
            f"（G5 skipped {item['G5_skipped']}；含 skipped 的 finding {item['gates_skipped_findings']} 条）"
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out_md.read_text(encoding="utf-8"))
    print(f"CSV: {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
