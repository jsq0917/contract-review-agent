"""单路 / 双路对照：同一批合同、同一语料、同一配置，只切换审查角色组合。

输出三件事：
1. 每种模式的风险点与耗时/成本；
2. 双路相对单路的**独有贡献**（法务独有、商务独有、两路共有）；
3. 每条独有风险点的原文回溯：clause_id + 引文 + risk_type。

用法::

    uv run python eval/run_dual_eval.py --engine mock
"""

from __future__ import annotations

import argparse
import csv
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
from lexflow.service import AnalyzeService  # noqa: E402

MANIFEST = ROOT / "eval" / "public_templates" / "manifest.json"
OUT_JSON = ROOT / "eval" / "dual_results.json"
OUT_MD = ROOT / "eval" / "dual_results.md"
OUT_CSV = ROOT / "eval" / "dual_unique_points.csv"

MODES: dict[str, tuple[str, ...]] = {
    "legal_only": ("legal",),
    "commercial_only": ("commercial",),
    "both": ("legal", "commercial"),
}


def group_points(findings: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """与 metrics.dual_review_stats 同一口径：条款 + 归一化引文。"""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for item in findings:
        key = (
            str(item.get("clause_id") or ""),
            metrics.normalize(str(item.get("original_quote") or "")),
        )
        entry = groups.setdefault(
            key,
            {
                "clause_id": key[0],
                "quote": item.get("original_quote") or "",
                "risk_types": set(),
                "reviewers": set(),
                "finding_ids": [],
            },
        )
        entry["risk_types"].add(str(item.get("risk_type") or "未分类"))
        entry["reviewers"].add(str(item.get("reviewer") or ""))
        entry["finding_ids"].append(str(item.get("finding_id") or ""))
    return groups


def run_mode(service: AnalyzeService, sample: Path, case: dict[str, Any], channels: tuple[str, ...], engine: str):
    result = service.analyze(
        sample,
        stance=case.get("stance", "甲方"),
        as_of=case.get("as_of"),
        engine=engine,
        channels=channels,
        require_confirmation=False,
        auto_approve=True,
    )
    report = result.report
    findings = report.get("findings", [])
    groups = group_points(findings)
    block = report.get("metrics", {})
    return {
        "channels": list(channels),
        "findings": len(findings),
        "points": groups,
        "latency_ms": result.elapsed_ms,
        "tokens": int(block.get("prompt_tokens", 0)) + int(block.get("completion_tokens", 0)),
        "cost_estimate_cny": block.get("cost_estimate_cny"),
        "trace_id": result.trace_id,
        "resolution": block.get("citation_resolution", {}),
    }


def compare_case(service: AnalyzeService, case: dict[str, Any], engine: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sample = ROOT / case["path"]
    runs = {name: run_mode(service, sample, case, channels, engine) for name, channels in MODES.items()}
    legal = set(runs["legal_only"]["points"])
    commercial = set(runs["commercial_only"]["points"])
    both = set(runs["both"]["points"])
    common = legal & commercial
    unique_legal = legal - commercial
    unique_commercial = commercial - legal
    union = legal | commercial

    rows: list[dict[str, Any]] = []
    for label, keys in (
        ("legal_only", unique_legal),
        ("commercial_only", unique_commercial),
        ("both_common", common),
    ):
        for key in sorted(keys):
            point = runs["both"]["points"].get(key) or runs["legal_only"]["points"].get(key) or runs["commercial_only"]["points"].get(key)
            rows.append(
                {
                    "sample": case["path"],
                    "case_id": case["id"],
                    "contribution": label,
                    "clause_id": point["clause_id"],
                    "quote": point["quote"],
                    "risk_types": "|".join(sorted(point["risk_types"])),
                    "finding_ids": "|".join(point["finding_ids"]),
                }
            )
    summary = {
        "id": case["id"],
        "split": case["split"],
        "sample": case["path"],
        "legal_only": {
            "risk_points": len(legal),
            "findings": runs["legal_only"]["findings"],
            "latency_ms": runs["legal_only"]["latency_ms"],
            "tokens": runs["legal_only"]["tokens"],
            "cost_estimate_cny": runs["legal_only"]["cost_estimate_cny"],
            "trace_id": runs["legal_only"]["trace_id"],
        },
        "commercial_only": {
            "risk_points": len(commercial),
            "findings": runs["commercial_only"]["findings"],
            "latency_ms": runs["commercial_only"]["latency_ms"],
            "tokens": runs["commercial_only"]["tokens"],
            "cost_estimate_cny": runs["commercial_only"]["cost_estimate_cny"],
            "trace_id": runs["commercial_only"]["trace_id"],
        },
        "both": {
            "risk_points": len(both),
            "findings": runs["both"]["findings"],
            "latency_ms": runs["both"]["latency_ms"],
            "tokens": runs["both"]["tokens"],
            "cost_estimate_cny": runs["both"]["cost_estimate_cny"],
            "trace_id": runs["both"]["trace_id"],
        },
        "complement": {
            "common": len(common),
            "legal_unique": len(unique_legal),
            "commercial_unique": len(unique_commercial),
            "union": len(union),
            "naive_sum": len(legal) + len(commercial),
            "both_matches_union": both == union,
        },
        "citation_resolution": runs["both"]["resolution"],
        "unique_rows": rows,
    }
    return summary, rows


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 单路 / 双路对照结果",
        "",
        f"- 生成时间：{payload['generated_at']}",
        f"- 引擎：`{payload['engine']}`",
        "- 控制变量：同一批合同、同一语料、同一检索配置；只切换 `channels`。",
        "- 风险点口径：`(clause_id, 归一化引文)`；`both_matches_union` 表示双路去重结果与两路并集一致。",
        "",
        "| case | 法务单路 | 商务单路 | 双路 | 两路共有 | 法务独有 | 商务独有 | 并集 | 简单相加 | 双路=并集 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in payload["cases"]:
        comp = row["complement"]
        lines.append(
            f"| {row['id']} | {row['legal_only']['risk_points']} | {row['commercial_only']['risk_points']} | "
            f"{row['both']['risk_points']} | {comp['common']} | {comp['legal_unique']} | "
            f"{comp['commercial_unique']} | {comp['union']} | {comp['naive_sum']} | "
            f"{'是' if comp['both_matches_union'] else '否'} |"
        )
    lines += [
        "",
        "## 成本与延迟",
        "",
        "| case | 模式 | 风险点 | findings | 耗时(ms) | tokens | 成本(CNY) | trace_id |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in payload["cases"]:
        for mode in ("legal_only", "commercial_only", "both"):
            item = row[mode]
            lines.append(
                f"| {row['id']} | {mode} | {item['risk_points']} | {item['findings']} | "
                f"{item['latency_ms']} | {item['tokens']} | "
                f"{'null' if item['cost_estimate_cny'] is None else item['cost_estimate_cny']} | `{item['trace_id']}` |"
            )
    lines += [
        "",
        "## 独有贡献溯源",
        "",
        "逐条见 `eval/dual_unique_points.csv`；每行都带 `clause_id`、原文引文与 `risk_types`。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="单路/双路对照")
    parser.add_argument("--engine", default="mock", choices=["mock", "api"])
    parser.add_argument("--manifest", default=str(MANIFEST))
    parser.add_argument("--out-json", default=str(OUT_JSON))
    parser.add_argument("--out-md", default=str(OUT_MD))
    parser.add_argument("--out-csv", default=str(OUT_CSV))
    args = parser.parse_args(argv)
    if args.engine == "api" and os.getenv("LEXFLOW_LIVE_API") != "1":
        print("拒绝执行：API 模式需显式设置 LEXFLOW_LIVE_API=1")
        return 2

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    service = AnalyzeService()
    cases: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    for case in manifest["cases"]:
        summary, rows = compare_case(service, case, args.engine)
        cases.append(summary)
        all_rows.extend(rows)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": args.engine,
        "label_source": manifest.get("label_source", "self_review"),
        "cases": cases,
    }
    Path(args.out_json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.out_md).write_text(render_markdown(payload), encoding="utf-8")
    with Path(args.out_csv).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["sample", "case_id", "contribution", "clause_id", "quote", "risk_types", "finding_ids"],
        )
        writer.writeheader()
        writer.writerows(all_rows)
    for row in cases:
        comp = row["complement"]
        print(
            f"{row['id']}: legal={row['legal_only']['risk_points']} commercial={row['commercial_only']['risk_points']} "
            f"both={row['both']['risk_points']} common={comp['common']} "
            f"legal_unique={comp['legal_unique']} commercial_unique={comp['commercial_unique']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
