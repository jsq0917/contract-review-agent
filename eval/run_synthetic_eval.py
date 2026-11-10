"""合成合同数据集评测：只输出原始计数与比率，不设目标值。

数据性质：合成合同；gold 由风险注入规则反推，非律师标注。
因此这里的 recall/precision 只回答「注入的风险条款是否被 mock 规则检出」，
不能外推为真实合同审查效果。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from docx import Document

import metrics
from lexflow.parse import parse_document
from lexflow.segment import segment_clauses
from lexflow.service import AnalyzeService
from lexflow.synthetic import article_no_of

SYNTH_DIR = ROOT / "data" / "synthetic"
GOLD = SYNTH_DIR / "gold.jsonl"
OUT_CSV = ROOT / "eval" / "synthetic_results.csv"
OUT_MD = ROOT / "eval" / "synthetic_results.md"

CSV_FIELDS = [
    "contract",
    "template",
    "variant",
    "expected_clauses",
    "parsed_clauses",
    "gold_risk_clauses",
    "hit_risk_clauses",
    "clause_recall",
    "found_risk_clauses",
    "clause_precision",
    "findings",
    "rule_hits",
    "traceable_findings",
    "traceability_rate",
    "cited_findings",
    "uncited_findings",
    "findings_all_gates_pass",
    "findings_all_gates_pass_rate",
    "findings_all_gates_pass_cited",
    "gates_skipped_findings",
    "risk_points",
    "merged_both",
    "legal_only",
    "commercial_only",
    "duplicate_findings",
    "G1_pass",
    "G1_total",
    "G2_pass",
    "G2_total",
    "G3_pass",
    "G3_total",
    "G4_pass",
    "G4_total",
    "G5_pass",
    "G5_total",
    "llm_calls",
    "prompt_tokens",
    "completion_tokens",
    "cost_estimate_cny",
    "elapsed_ms",
    "docx_table_rows",
    "db_findings",
    "note",
]


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def compute_entry(service: AnalyzeService, row: dict, gold_path: Path) -> dict:
    sample = gold_path.parent / row["file"]
    result = service.analyze(
        sample,
        stance=row.get("stance", "甲方"),
        as_of=row.get("as_of"),
        engine="mock",
        require_confirmation=False,
        auto_approve=True,
    )
    report = result.report
    findings = report.get("findings", [])
    verifications = report.get("verifications", [])

    parsed = parse_document(sample)
    clauses = segment_clauses(parsed)
    clause_text = {clause.clause_id: clause.text for clause in clauses}
    article_by_clause = {
        clause.clause_id: article_no_of(clause.text) for clause in clauses
    }

    gold_articles = set(row["expected_risk_article_nos"])
    found_articles = {
        article_by_clause.get(finding.get("clause_id", "")) for finding in findings
    }
    found_articles.discard(None)
    hit_articles = gold_articles & found_articles

    traceable = sum(1 for finding in findings if metrics.strict_traceable(finding, clause_text))
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
    all_gates_pass = gates["all_pass"]

    docx_path = Path(result.docx_path or "")
    docx_rows = 0
    if docx_path.exists():
        docx_rows = len(Document(docx_path).tables[0].rows) - 1
    return {
        "contract": Path(row["file"]).name,
        "template": row["template"],
        "variant": row["variant"],
        "expected_clauses": row["expected_clause_count"],
        "parsed_clauses": len(clauses),
        "gold_risk_clauses": len(gold_articles),
        "hit_risk_clauses": len(hit_articles),
        "clause_recall": _ratio(len(hit_articles), len(gold_articles)),
        "found_risk_clauses": len(found_articles),
        "clause_precision": _ratio(len(hit_articles), len(found_articles)),
        "findings": len(findings),
        "rule_hits": json.dumps(
            dict(
                sorted(
                    Counter(
                        str(finding.get("rule_id") or "unclassified") for finding in findings
                    ).items()
                )
            ),
            ensure_ascii=False,
        ),
        "traceable_findings": traceable,
        "traceability_rate": _ratio(traceable, len(findings)),
        "cited_findings": gates["cited"],
        "uncited_findings": gates["uncited"],
        "findings_all_gates_pass": all_gates_pass,
        "findings_all_gates_pass_rate": _ratio(all_gates_pass, len(findings)),
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
        "llm_calls": result.raw_state.get("llm_calls", 0),
        "prompt_tokens": result.raw_state.get("prompt_tokens", 0),
        "completion_tokens": result.raw_state.get("completion_tokens", 0),
        "cost_estimate_cny": (report.get("metrics") or {}).get("cost_estimate_cny"),
        "elapsed_ms": result.elapsed_ms,
        "docx_table_rows": docx_rows,
        "db_findings": service.store.count_findings(result.matter_id),
        "note": "合成数据；gold 由注入规则反推；mock 引擎，指标不可外推",
    }


def _fmt(value: float | None) -> str:
    return "不可计算" if value is None else str(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="合成合同评测（注入式 gold）")
    parser.add_argument("--gold", default=str(GOLD), help="gold.jsonl 路径")
    parser.add_argument("--out-csv", default=str(OUT_CSV), help="输出 CSV")
    parser.add_argument("--out-md", default=str(OUT_MD), help="输出 Markdown")
    parser.add_argument("--label", default="", help="标题中的标签，如 before/v1-after/v2-after")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    gold = Path(args.gold)
    out_csv = Path(args.out_csv)
    out_md = Path(args.out_md)
    if not gold.exists() or not gold.read_text(encoding="utf-8").strip():
        message = "无标注数据，指标不可计算"
        print(message)
        out_md.write_text(f"# 合成合同评测\n\n{message}\n", encoding="utf-8")
        return 0

    rows = [json.loads(line) for line in gold.read_text(encoding="utf-8").splitlines() if line.strip()]
    service = AnalyzeService()
    results = [compute_entry(service, row, gold) for row in rows]

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(results)

    totals = {
        "gold_risk_clauses": sum(item["gold_risk_clauses"] for item in results),
        "hit_risk_clauses": sum(item["hit_risk_clauses"] for item in results),
        "found_risk_clauses": sum(item["found_risk_clauses"] for item in results),
        "findings": sum(item["findings"] for item in results),
        "traceable_findings": sum(item["traceable_findings"] for item in results),
        "cited_findings": sum(item["cited_findings"] for item in results),
        "uncited_findings": sum(item["uncited_findings"] for item in results),
        "findings_all_gates_pass": sum(item["findings_all_gates_pass"] for item in results),
        "findings_all_gates_pass_cited": sum(
            item["findings_all_gates_pass_cited"] for item in results
        ),
        "risk_points": sum(item["risk_points"] for item in results),
        "merged_both": sum(item["merged_both"] for item in results),
        "legal_only": sum(item["legal_only"] for item in results),
        "commercial_only": sum(item["commercial_only"] for item in results),
        "duplicate_findings": sum(item["duplicate_findings"] for item in results),
        "llm_calls": sum(item["llm_calls"] for item in results),
        "prompt_tokens": sum(item["prompt_tokens"] for item in results),
        "completion_tokens": sum(item["completion_tokens"] for item in results),
    }
    rule_totals: Counter[str] = Counter()
    for item in results:
        rule_totals.update(json.loads(item["rule_hits"]))
    micro_recall = _ratio(totals["hit_risk_clauses"], totals["gold_risk_clauses"])
    micro_precision = _ratio(totals["hit_risk_clauses"], totals["found_risk_clauses"])
    trace_rate = _ratio(totals["traceable_findings"], totals["findings"])
    gates_rate = _ratio(totals["findings_all_gates_pass"], totals["findings"])

    title = "# 合成合同评测（原始计数与比率）"
    if args.label:
        title += f" — {args.label}"
    lines = [
        title,
        "",
        f"> 数据集：`{gold}`",
        "> 数据性质：**合成合同**（模板 + 固定种子 + 风险条款注入）；gold 由注入规则反推，非律师标注。",
        "> mock 引擎下，以下数字只说明「注入风险是否被规则检出」与链路是否跑通，**不可外推到真实合同效果**。",
        "",
        "| 合同 | 条款数 | gold风险条款 | 命中 | 条款召回 | 检出条款 | 条款precision | findings | 严格溯源率 | 引用/未引用 | 五闸全过率 | 双路：风险点/共同/法务独有/商务独有 | 耗时ms | docx行数 | DB条数 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in results:
        lines.append(
            f"| {item['contract']} | {item['parsed_clauses']}/{item['expected_clauses']} | "
            f"{item['gold_risk_clauses']} | {item['hit_risk_clauses']} | {_fmt(item['clause_recall'])} | "
            f"{item['found_risk_clauses']} | {_fmt(item['clause_precision'])} | {item['findings']} | "
            f"{item['traceable_findings']}/{item['findings']} ({_fmt(item['traceability_rate'])}) | "
            f"{item['cited_findings']}/{item['uncited_findings']} | "
            f"{item['findings_all_gates_pass']}/{item['findings']} ({_fmt(item['findings_all_gates_pass_rate'])}) | "
            f"{item['risk_points']}/{item['merged_both']}/{item['legal_only']}/{item['commercial_only']} | "
            f"{item['elapsed_ms']} | {item['docx_table_rows']} | {item['db_findings']} |"
        )

    lines += [
        "",
        "## 汇总（微平均，仅本次运行）",
        f"- 条款召回：{totals['hit_risk_clauses']}/{totals['gold_risk_clauses']} = {_fmt(micro_recall)}",
        f"- 条款 precision：{totals['hit_risk_clauses']}/{totals['found_risk_clauses']} = {_fmt(micro_precision)}",
        f"- 严格溯源率（original_quote 是条款正文子串）：{totals['traceable_findings']}/{totals['findings']} = {_fmt(trace_rate)}",
        f"- 引用/未引用：{totals['cited_findings']}/{totals['uncited_findings']}",
        f"- 五闸全过率（skipped 不计通过）：{totals['findings_all_gates_pass']}/{totals['findings']} = {_fmt(gates_rate)}"
        f"（其中有引用的 {totals['findings_all_gates_pass_cited']} 条）",
        f"- 双路互补：风险点 {totals['risk_points']}（原始 findings {totals['findings']}，重复 {totals['duplicate_findings']}）；"
        f"两路共同 {totals['merged_both']}，法务独有 {totals['legal_only']}，商务独有 {totals['commercial_only']}",
        f"- 调用次数：{totals['llm_calls']}；tokens(in/out)：{totals['prompt_tokens']}/{totals['completion_tokens']}",
        "",
        "## 各规则命中计数（按 findings 计数，跨合同累加）",
        "| 规则 ID | 命中 findings 数 |",
        "|---|---|",
    ]
    for rule_id, count in sorted(rule_totals.items()):
        lines.append(f"| {rule_id} | {count} |")
    lines += [
        "",
        "## 分闸门计数",
    ]
    for item in results:
        lines.append(
            f"- {item['contract']}: G1 {item['G1_pass']}/{item['G1_total']}；"
            f"G2 {item['G2_pass']}/{item['G2_total']}；G3 {item['G3_pass']}/{item['G3_total']}；"
            f"G4 {item['G4_pass']}/{item['G4_total']}；G5 {item['G5_pass']}/{item['G5_total']}"
        )
    lines += ["", f"CSV: {out_csv}", "", "> 以上均非真实合同分布，任何比率不得作为效果宣传。"]
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out_md.read_text(encoding="utf-8"))
    print(f"CSV: {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
