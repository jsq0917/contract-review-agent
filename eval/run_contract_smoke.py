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
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lexflow.models import Finding
from lexflow.parse import parse_document
from lexflow.segment import segment_clauses
from lexflow.service import AnalyzeService

OUT_MD = ROOT / "eval" / "contract_smoke_results.md"
OUT_JSON = ROOT / "eval" / "contract_smoke_results.json"

PUNCT_RE = re.compile(r"\s+|[，。；：、,.!?;:\"'“”‘’（）()《》【】\[\]…—\-]")
GATES = ("G1", "G2", "G3", "G4", "G5")
SUFFIXES = {".docx", ".pdf"}


def normalize(text: str) -> str:
    return PUNCT_RE.sub("", text or "")


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

    traceable = 0
    for finding in findings:
        source = clause_text.get(finding.clause_id, "")
        quote = normalize(finding.original_quote)
        if quote and quote in normalize(source):
            traceable += 1

    cited = sum(1 for finding in findings if finding.law_name and finding.article_no)
    verifications = report.get("verifications", [])
    gate_counts = {
        gate: {
            "pass": sum(1 for item in verifications if item["gate"] == gate and item["passed"]),
            "total": sum(1 for item in verifications if item["gate"] == gate),
        }
        for gate in GATES
    }
    all_pass = sum(
        1
        for finding in findings
        if all(
            item["passed"]
            for item in verifications
            if item["finding_id"] == finding.finding_id
        )
        and sum(1 for item in verifications if item["finding_id"] == finding.finding_id) == len(GATES)
    )

    metrics = report.get("metrics", {}) or {}
    return {
        "file": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        "engine": result.engine,
        "chars": len(parsed.full_text),
        "clauses": len(clauses),
        "avg_clause_chars": round(len(parsed.full_text) / len(clauses), 1) if clauses else 0,
        "findings": len(findings),
        "traceable": traceable,
        "traceability_rate": round(traceable / len(findings), 4) if findings else None,
        "cited": cited,
        "citation_coverage": round(cited / len(findings), 4) if findings else None,
        "all_gates_pass": all_pass,
        "all_gates_pass_rate": round(all_pass / len(findings), 4) if findings else None,
        "gate_counts": gate_counts,
        "llm_calls": metrics.get("llm_calls", 0),
        "prompt_tokens": metrics.get("prompt_tokens", 0),
        "completion_tokens": metrics.get("completion_tokens", 0),
        "cost_cny": metrics.get("cost_estimate_cny"),
        "elapsed_ms": metrics.get("node_latency_ms_total", 0),
        "retrieval_mode": metrics.get("retrieval_mode", ""),
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
        "| 合同 | 字符 | 条款 | findings | 溯源率 | 引用覆盖 | 五闸全过 | tokens(in/out) | 成本 | 耗时ms |",
        "|---|---:|---:|---:|---:|---:|---:|---|---:|---:|",
    ]
    for item in rows:
        trace = f"{item['traceable']}/{item['findings']} ({item['traceability_rate']})" if item["findings"] else "—"
        cover = f"{item['cited']}/{item['findings']} ({item['citation_coverage']})" if item["findings"] else "—"
        gates = f"{item['all_gates_pass']}/{item['findings']}" if item["findings"] else "—"
        cost = "0" if item["cost_cny"] in (None, 0) else f"¥{item['cost_cny']:.4f}"
        lines.append(
            f"| {Path(item['file']).name} | {item['chars']} | {item['clauses']} | {item['findings']} | "
            f"{trace} | {cover} | {gates} | {item['prompt_tokens']}/{item['completion_tokens']} | "
            f"{cost} | {item['elapsed_ms']} |"
        )

    lines += ["", "## 分闸门通过数", ""]
    for item in rows:
        detail = "；".join(
            f"{gate} {item['gate_counts'][gate]['pass']}/{item['gate_counts'][gate]['total']}" for gate in GATES
        )
        lines.append(f"- {Path(item['file']).name}: {detail}")

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_MD.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
