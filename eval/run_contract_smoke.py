"""合同冒烟评测：只输出**不依赖 gold** 的指标。

用途：对任意合同（真实 / 合成 / 示例）跑一遍分析，看切分、溯源、闸门与成本。
这类指标不需要人工标注，适合在样本扩充期先确认「管线在没见过的文档上有没有退化」，
不必等 gold 标完。

用法：
    python eval/run_contract_smoke.py --dir examples/real --engine mock
    python eval/run_contract_smoke.py --files examples/real/labor_contract.docx --engine api

口径说明：
- 溯源率（严格）= finding 的 original_quote 归一化后是该条款正文的子串。比
  `run_eval.py` 里「clause_id 能对上」更严，后者只证明条款存在，不证明引文真实。
- 引用覆盖率 = 带 law_name 的 finding 占比。真实模型会主动拒引，这个数天然低于 1，
  不是缺陷，见 README §4.6。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import metrics
from lexflow.models import Finding
from lexflow.parse import parse_document
from lexflow.segment import segment_clauses
from lexflow.service import AnalyzeService

OUT_MD = ROOT / "eval" / "contract_smoke_results.md"
OUT_JSON = ROOT / "eval" / "contract_smoke_results.json"

SUFFIXES = {".docx", ".pdf"}


def collect_files(args: argparse.Namespace) -> list[Path]:
    if args.files:
        return [Path(item) for item in args.files]
    directory = Path(args.dir)
    if not directory.exists():
        raise SystemExit(f"目录不存在：{directory}")
    return sorted(p for p in directory.iterdir() if p.suffix.lower() in SUFFIXES)


def smoke_one(service: AnalyzeService, path: Path, engine: str) -> dict:
    parsed = parse_document(path)
    clauses = segment_clauses(parsed)
    clause_text = {item.clause_id: item.text for item in clauses}

    result = service.analyze(
        path,
        stance="甲方",
        as_of="2026-11-03",
        engine=engine,
        require_confirmation=False,
        auto_approve=True,
    )
    report = result.report
    fields = set(Finding.model_fields)
    findings = [
        Finding(**{key: value for key, value in item.items() if key in fields})
        for item in report.get("findings", [])
    ]

    findings_as_dict = [
        finding.model_dump() for finding in findings
    ]
    traceable = sum(1 for finding in findings if metrics.strict_traceable(finding.model_dump(), clause_text))

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
    gates = metrics.gates_summary(findings_as_dict, verifications)
    dual = metrics.dual_review_stats(findings_as_dict)

    report_metrics = report.get("metrics", {}) or {}
    return {
        "file": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        "engine": result.engine,
        "chars": len(parsed.full_text),
        "clauses": len(clauses),
        "avg_clause_chars": round(len(parsed.full_text) / len(clauses), 1) if clauses else 0,
        "findings": len(findings),
        "traceable": traceable,
        "traceability_rate": round(traceable / len(findings), 4) if findings else None,
        "cited": gates["cited"],
        "uncited": gates["uncited"],
        "citation_coverage": round(gates["cited"] / len(findings), 4) if findings else None,
        "all_gates_pass": gates["all_pass"],
        "all_gates_pass_rate": round(gates["all_pass"] / len(findings), 4) if findings else None,
        "all_gates_pass_cited": gates["all_pass_cited"],
        "gates_skipped_findings": gates["skipped_findings"],
        "risk_points": dual["risk_points"],
        "merged_both": dual["merged_both"],
        "legal_only": dual["legal_only"],
        "commercial_only": dual["commercial_only"],
        "duplicate_findings": dual["duplicates"],
        "gate_counts": gate_counts,
        "llm_calls": report_metrics.get("llm_calls", 0),
        "prompt_tokens": report_metrics.get("prompt_tokens", 0),
        "completion_tokens": report_metrics.get("completion_tokens", 0),
        "cost_cny": report_metrics.get("cost_estimate_cny"),
        "elapsed_ms": report_metrics.get("node_latency_ms_total", 0),
        "retrieval_mode": report_metrics.get("retrieval_mode", ""),
        "trace_id": report_metrics.get("trace_id", ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="合同冒烟评测（不依赖 gold）")
    parser.add_argument("--dir", default="examples/real", help="合同目录")
    parser.add_argument("--files", nargs="*", help="显式指定文件，优先于 --dir")
    parser.add_argument("--engine", default="mock", choices=["mock", "api"])
    args = parser.parse_args()

    files = collect_files(args)
    if not files:
        raise SystemExit("没有找到可评测的合同文件")

    service = AnalyzeService()
    rows = [smoke_one(service, path, args.engine) for path in files]

    OUT_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 合同冒烟评测（不依赖 gold）",
        "",
        f"> 引擎 {args.engine}｜样本 {len(rows)} 份｜只描述本次运行，不代表法律质量。",
        "> 溯源率按「引文是条款正文子串」严格判定；引用覆盖率低是模型主动拒引，见 README §4.6。",
        "",
        "| 合同 | 字符 | 条款 | findings | 严格溯源率 | 引用/未引用 | 五闸全过 | 双路：风险点/共同/法务独有/商务独有 | tokens(in/out) | 成本 | 耗时ms |",
        "|---|---:|---:|---:|---:|---|---:|---|---|---:|---:|",
    ]
    for item in rows:
        trace = f"{item['traceable']}/{item['findings']} ({item['traceability_rate']})" if item["findings"] else "—"
        cover = f"{item['cited']}/{item['uncited']}" if item["findings"] else "—"
        gates = f"{item['all_gates_pass']}/{item['findings']}" if item["findings"] else "—"
        dual = f"{item['risk_points']}/{item['merged_both']}/{item['legal_only']}/{item['commercial_only']}"
        cost = "0" if item["cost_cny"] in (None, 0) else f"¥{item['cost_cny']:.4f}"
        lines.append(
            f"| {Path(item['file']).name} | {item['chars']} | {item['clauses']} | {item['findings']} | "
            f"{trace} | {cover} | {gates} | {dual} | {item['prompt_tokens']}/{item['completion_tokens']} | "
            f"{cost} | {item['elapsed_ms']} |"
        )

    lines += ["", "> 五闸全过要求五道闸都是 pass，skipped 不算通过；API 引擎的 G4 固定为 skipped（不校验法条适用性）。", ""]
    lines += ["", "## 分闸门通过数", ""]
    for item in rows:
        detail = "；".join(
            f"{gate} {item['gate_counts'][gate]['pass']}/{item['gate_counts'][gate]['total']}"
            for gate in metrics.GATES
        )
        lines.append(f"- {Path(item['file']).name}: {detail}（含 skipped 的 finding {item['gates_skipped_findings']} 条）")

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_MD.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
