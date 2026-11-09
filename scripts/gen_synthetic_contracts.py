"""生成合成合同数据集（可复现）。

性质：合成数据，非真实合同；gold 由风险注入规则反推，非律师标注。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lexflow.synthetic import DEFAULT_SEED, generate_dataset


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成 lexflow 合成合同数据集")
    parser.add_argument("--out", default=str(ROOT / "data" / "synthetic"), help="输出目录")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="随机种子（整数）")
    parser.add_argument(
        "--profile",
        choices=("v1", "v2"),
        default="v2",
        help="v1=阶段 1 原始 4 类风险；v2=扩展 8 类风险",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out)
    records = generate_dataset(out_dir, seed=args.seed, profile=args.profile)
    risk_total = sum(len(record.risks) for record in records)
    print(f"profile={args.profile}")
    print(f"已生成 {len(records)} 份合成合同，注入风险条款 {risk_total} 处")
    print(f"合同目录: {out_dir / 'contracts'}")
    print(f"gold: {out_dir / 'gold.jsonl'}")
    print(f"manifest: {out_dir / 'manifest.json'}")
    print("性质: 合成数据，非真实合同分布；gold 由注入规则反推，非律师标注。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
