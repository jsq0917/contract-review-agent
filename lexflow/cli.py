"""CLI：无 API key 也能用 mock 端到端跑通。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from lexflow.service import AnalyzeService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lexflow", description="lexflow M0 合同审查")
    sub = parser.add_subparsers(dest="command", required=True)
    analyze = sub.add_parser("analyze", help="分析一份合同并导出 docx")
    analyze.add_argument("file")
    analyze.add_argument("--engine", default="auto", choices=["auto", "mock", "api"])
    analyze.add_argument("--stance", default="甲方")
    analyze.add_argument("--as-of", default=date.today().isoformat())
    analyze.add_argument("--require-confirmation", action="store_true")
    analyze.add_argument("--auto-approve", action="store_true", help="跳过交互确认（用于自动化验证）")
    timeline = sub.add_parser("timeline", help="按 matter_id 查看节点耗时与 token")
    timeline.add_argument("matter_id")
    return parser


def _timeline(matter_id: str) -> int:
    from lexflow.config import get_settings
    from lexflow.storage.db import Store

    store = Store(get_settings().db_path)
    matter = store.get_matter(matter_id)
    if matter is None:
        print(json.dumps({"matter_id": matter_id, "error": "matter not found"}, ensure_ascii=False))
        return 2
    result = json.loads(matter["result_json"]) if matter.get("result_json") else {}
    metrics = result.get("metrics", {})
    node_latency = metrics.get("node_latency_ms", {})
    print(
        json.dumps(
            {
                "matter_id": matter_id,
                "status": matter["status"],
                "engine": result.get("engine"),
                "stance": result.get("stance"),
                "doc_id": result.get("doc_id"),
                "findings": len(result.get("findings", [])),
                "llm_calls": metrics.get("llm_calls"),
                "prompt_tokens": metrics.get("prompt_tokens"),
                "completion_tokens": metrics.get("completion_tokens"),
                "cost_estimate_cny": metrics.get("cost_estimate_cny"),
                "retrieval_mode": metrics.get("retrieval_mode"),
                "retrieval_device": metrics.get("retrieval_device"),
                "node_latency_ms": node_latency,
                "total_node_ms": sum(node_latency.values()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "timeline":
        return _timeline(args.matter_id)
    if args.command == "analyze":
        service = AnalyzeService()
        auto = args.auto_approve or not args.require_confirmation
        # 默认走单人确认（interrupt）；--auto-approve 仅用于自动化验证。
        result = service.analyze(
            args.file,
            stance=args.stance,
            as_of=args.as_of,
            engine=args.engine,
            require_confirmation=not args.auto_approve,
            auto_approve=args.auto_approve,
        )
        if result.status == "awaiting_review":
            print(json.dumps({"matter_id": result.matter_id, "status": result.status}, ensure_ascii=False))
            answer = input("确认风险清单并导出 docx？[y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                print("已保留 checkpoint，可稍后调用 service.review 恢复。")
                return 1
            result = service.review(result.matter_id, action="approve", comment="CLI 单人确认")
        print(
            json.dumps(
                {
                    "matter_id": result.matter_id,
                    "status": result.status,
                    "engine": result.engine,
                    "findings_count": result.findings_count,
                    "elapsed_ms": result.elapsed_ms,
                    "docx_path": result.docx_path,
                    "warnings": result.warnings,
                    "metrics": result.report.get("metrics", {}),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
