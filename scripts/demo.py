"""离线演示：上传示例 → mock 分析 → 风险清单 → 确认 → 导出 docx → 指标。

全程不联网、不需要 API key；走的是 CLI/API/UI 共用的 AnalyzeService。
用法：
    uv run python scripts/demo.py
    uv run python scripts/demo.py --file examples/sample_procurement_contract.pdf --stance 乙方
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 演示面向投屏：默认压制 INFO 日志与模型加载进度条；需要日志证据时加 --show-logs。
os.environ.setdefault("LEXFLOW_LOG_LEVEL", "ERROR")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

from lexflow.service import AnalyzeService  # noqa: E402

RISK_ORDER = {"high": 0, "medium": 1, "low": 2}
RISK_LABEL = {"high": "HIGH", "medium": "MED", "low": "LOW"}
BAR = "-" * 78


def _step(index: int, total: int, title: str) -> None:
    print(f"\n[{index}/{total}] {title}")
    print(BAR)


def _law_display(item: dict) -> str:
    return " ".join(part for part in (item.get("law_name"), item.get("article_no")) if part) or "无"


def _locator(item: dict) -> str:
    return " · ".join(part for part in (item.get("doc_id"), item.get("locator")) if part) or "无定位"


def _sorted_findings(findings: list[dict]) -> list[dict]:
    return sorted(
        findings,
        key=lambda item: (RISK_ORDER.get(item.get("risk_level"), 9), item.get("clause_id", "")),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="lexflow 离线演示（engine=mock）")
    parser.add_argument(
        "--file",
        default=str(ROOT / "examples" / "sample_service_contract.docx"),
        help="示例合同路径（默认 examples/sample_service_contract.docx）",
    )
    parser.add_argument("--stance", default="甲方", choices=["甲方", "乙方"])
    parser.add_argument("--as-of", default="2026-11-03")
    parser.add_argument("--comment", default="演示确认", help="人工确认意见")
    parser.add_argument("--show-logs", action="store_true", help="保留 INFO 级 JSON 日志（默认只显示错误）")
    args = parser.parse_args(argv)

    if args.show_logs:
        import logging

        for name in list(logging.Logger.manager.loggerDict):
            if name.startswith("lexflow"):
                logging.getLogger(name).setLevel(logging.INFO)

    source = Path(args.file)
    if not source.exists():
        print(f"文件不存在: {source}", file=sys.stderr)
        return 2

    total = 6
    print("lexflow M0 离线演示（engine=mock）")
    print("提示：本演示使用固定规则表，仅验证链路与界面，不代表真实模型效果，也不构成法律意见。")

    _step(1, total, "上传示例合同")
    raw = source.read_bytes()
    print(f"文件: {source}")
    print(f"大小: {len(raw)} B   SHA256: {hashlib.sha256(raw).hexdigest()}")

    service = AnalyzeService()
    _step(2, total, "mock 分析（离线，无 API key）")
    result = service.analyze(
        source,
        stance=args.stance,
        as_of=args.as_of,
        engine="mock",
        require_confirmation=True,
        auto_approve=False,
    )
    print(f"matter_id: {result.matter_id}")
    print(f"engine: {result.engine}    status: {result.status}    端到端耗时: {result.elapsed_ms} ms")
    print(f"doc_id: {result.report.get('doc_id', '')}")
    if result.warnings:
        for warning in result.warnings:
            print(f"warning: {warning}")

    findings = _sorted_findings(result.report.get("findings", []))
    _step(3, total, f"风险清单（{len(findings)} 条，按 high > medium > low 排序）")
    if not findings:
        print("（未命中风险规则）")
    else:
        print("说明：法务、商务两路已按 (clause_id, risk_type, 引文) 合并；同一条款的不同风险类型或不同引文仍会分列。")
        print(BAR)
    reviewer_label = {"legal": "法务", "commercial": "商务"}
    for index, item in enumerate(findings, 1):
        level = RISK_LABEL.get(item.get("risk_level"), "?")
        reviewer = reviewer_label.get(item.get("reviewer", ""), item.get("reviewer", ""))
        print(
            f"#{index}  {level:<4} 规则 {item.get('rule_id') or '无':<10} "
            f"条款 {item.get('clause_id')}（{reviewer}）"
        )
        print(f"    法条  {_law_display(item)}")
        print(f"    定位  {_locator(item)}")
        print(f"    问题  {item.get('issue', '')}")
        print(f"    建议  {item.get('suggestion', '')}")

    _step(4, total, "人工确认（interrupt 后 resume）")
    print(f"action: approve    comment: {args.comment}")
    resumed = service.review(result.matter_id, action="approve", comment=args.comment)
    print(f"status: {resumed.status}    findings: {resumed.findings_count}")

    _step(5, total, "导出 docx")
    docx_path = Path(resumed.docx_path or "")
    if not docx_path.exists():
        print("导出失败：docx 不存在", file=sys.stderr)
        return 1
    print(f"路径: {docx_path}")
    print(f"大小: {docx_path.stat().st_size} B")

    metrics = resumed.report.get("metrics", {})
    node_ms = metrics.get("node_latency_ms", {})
    _step(6, total, "本次实测指标（真实计数，非估算）")
    print(
        f"总耗时（分析 + 确认/导出）: {result.elapsed_ms + resumed.elapsed_ms} ms"
        f"（分析 {result.elapsed_ms} ms，含检索冷启动；确认/导出 {resumed.elapsed_ms} ms）"
    )
    print(
        "LLM 调用: {calls}（mock 每次审查计一次，token 恒 0）"
        "    prompt tokens: {prompt}    completion tokens: {completion}"
        "    成本: {cost} 元".format(
            calls=metrics.get("llm_calls", 0),
            prompt=metrics.get("prompt_tokens", 0),
            completion=metrics.get("completion_tokens", 0),
            cost=metrics.get("cost_estimate_cny", 0.0),
        )
    )
    print(f"检索模式: {metrics.get('retrieval_mode')}    设备: {metrics.get('retrieval_device')}")
    print("节点耗时:")
    for name, value in node_ms.items():
        print(f"  {name:<16} {value:>6} ms")
    print(f"节点合计: {sum(node_ms.values())} ms")
    print(BAR)
    print("完成。复跑: uv run python scripts/demo.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
