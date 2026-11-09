"""统计 failures.jsonl：按 category / node / retriable 输出真实计数。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lexflow.failures import summarize


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="统计 lexflow 失败归档")
    parser.add_argument(
        "--path",
        default=str(ROOT / "data" / "failures.jsonl"),
        help="failures.jsonl 路径",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = summarize(args.path)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    print(f"文件: {summary['path']}")
    print(f"总失败数: {summary['total']}")
    print("按 category:")
    for name, count in summary["by_category"].items():
        print(f"  {name}: {count}")
    print("按 node:")
    for name, count in summary["by_node"].items():
        print(f"  {name}: {count}")
    print(f"retriable: true={summary['by_retriable']['true']} false={summary['by_retriable']['false']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
