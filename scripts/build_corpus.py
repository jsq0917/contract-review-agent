"""构建统一法条语料：民法典 1260 + 劳动法 107 + 劳动合同法 98。

数据全部来自 E:\\project；元数据缺失项见 data/corpus/corpus_manifest.json。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lexflow.corpus_build import build_corpus


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="构建 lexflow 统一法条语料")
    parser.add_argument("--out", default=str(ROOT / "data" / "corpus"), help="输出目录")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = build_corpus(Path(args.out))
    print(f"总条数: {manifest['total']}")
    for item in manifest["by_law"]:
        print(f"  {item['law_id']} {item['law_name']}: {item['count']} 条")
    normalization = manifest["normalization"]
    print(
        f"劳动法康熙部首: 源文件 {normalization['source_chars']} 字，"
        f"正文 {normalization['article_chars']} 字，影响 {normalization['affected_articles']} 条"
    )
    print(f"语料: {Path(args.out) / 'law_articles.jsonl'}")
    print(f"归一化记录: {Path(args.out) / 'labor_law_normalization.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
