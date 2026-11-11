"""检索评测：只用能对齐到法条条号的 QA 标注，输出 dense / hybrid / hybrid+rerank 原始命中。

数据来自 E:\\project 内既有评测集：
- LawAnswer\\eval\\qa_test_cases.jsonl
- LexSynergy Legal QA System\\eval\\qa_set_100.jsonl
- LexSynergy Legal QA System\\eval\\qa_set_130.jsonl
- LexWeave\\data\\gold\\gold_200.jsonl

对齐规则：
1. QA 集的 expected_articles 形如「中华人民共和国劳动合同法#第十九条」，按法名映射 law_id 后必须能在语料中找到。
2. LexWeave gold 形如「lcl#30」，阿拉伯条号转中文条号后必须能找到。
3. 一条问题的所有标注条号都能对齐才纳入；否则整条剔除并计数。
4. 按 question 文本去重（qa_set_100 完全包含于 qa_set_130，qa_test_cases 与它们只有部分交集）。

只输出原始命中计数与 Recall@5/10，不设目标值，也不据此下结论。
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lexflow.config import get_settings
from lexflow.retrieval.corpus import LAW_ID_BY_NAME, LawArticle, load_corpus
from lexflow.retrieval.hybrid import HybridRetriever

OUT_CSV = ROOT / "eval" / "retrieval_results.csv"
OUT_MD = ROOT / "eval" / "retrieval_results.md"

SOURCES = [
    ("lawanswer_120", Path(r"E:\project\LawAnswer\eval\qa_test_cases.jsonl"), "qa"),
    (
        "lexsynergy_100",
        Path(r"E:\project\LexSynergy Legal QA System\eval\qa_set_100.jsonl"),
        "qa",
    ),
    (
        "lexsynergy_130",
        Path(r"E:\project\LexSynergy Legal QA System\eval\qa_set_130.jsonl"),
        "qa",
    ),
    ("lexweave_200", Path(r"E:\project\LexWeave\data\gold\gold_200.jsonl"), "gold"),
]

CSV_FIELDS = [
    "question_id",
    "sources",
    "question",
    "gold_count",
    "gold_keys",
    "dense_hit5",
    "dense_hit10",
    "dense_top5",
    "hybrid_hit5",
    "hybrid_hit10",
    "hybrid_top5",
    "rerank_hit5",
    "rerank_hit10",
    "rerank_top5",
]


def int_to_cn(number: int) -> str:
    """阿拉伯条号转法条常用中文写法（1..1260 已对语料穷举校验）。"""
    digits = "零一二三四五六七八九"
    units = ["", "十", "百", "千"]
    if number < 10:
        return digits[number]
    text = ""
    pending_zero = False
    length = len(str(number))
    for index, char in enumerate(str(number)):
        digit = int(char)
        position = length - index - 1
        if digit == 0:
            pending_zero = True
            continue
        if pending_zero and text:
            text += "零"
        pending_zero = False
        text += digits[digit] + units[position]
    if text.startswith("一十"):
        text = text[1:]
    return text


def resolve_qa_ref(raw: str, available: set[tuple[str, str]]) -> tuple[str, str] | None:
    if "#" not in raw:
        return None
    law_name, article_no = raw.split("#", 1)
    law_id = LAW_ID_BY_NAME.get(law_name)
    if not law_id:
        return None
    key = (law_id, article_no)
    return key if key in available else None


def resolve_gold_ref(raw: str, available: set[tuple[str, str]]) -> tuple[str, str] | None:
    if "#" not in raw:
        return None
    law_id, number = raw.split("#", 1)
    if not number.isdigit():
        return None
    article_no = f"第{int_to_cn(int(number))}条"
    key = (law_id, article_no)
    return key if key in available else None


@dataclass
class Case:
    question_id: str
    question: str
    gold: set[tuple[str, str]]
    sources: list[str] = field(default_factory=list)


def load_cases(articles: list[LawArticle]) -> tuple[list[Case], list[dict]]:
    available = {(article.law_id, article.article_no) for article in articles}
    cases: list[Case] = []
    seen: dict[str, Case] = {}
    reports: list[dict] = []
    for name, path, kind in SOURCES:
        report = {
            "source": name,
            "path": str(path),
            "sha256": "",
            "rows": 0,
            "aligned": 0,
            "excluded": 0,
            "excluded_reasons": {"empty_expected": 0, "unresolved_ref": 0, "missing_file": 0},
        }
        if not path.exists():
            report["excluded_reasons"]["missing_file"] += 1
            reports.append(report)
            continue
        report["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        report["rows"] = len(rows)
        resolver = resolve_qa_ref if kind == "qa" else resolve_gold_ref
        for row in rows:
            raw_refs = row.get("expected_articles") or []
            if not raw_refs:
                report["excluded"] += 1
                report["excluded_reasons"]["empty_expected"] += 1
                continue
            gold = {resolver(ref, available) for ref in raw_refs}
            if None in gold:
                report["excluded"] += 1
                report["excluded_reasons"]["unresolved_ref"] += 1
                continue
            gold = {ref for ref in gold if ref is not None}
            report["aligned"] += 1
            question = str(row.get("question", "")).strip()
            if question in seen:
                seen[question].sources.append(name)
                continue
            case = Case(
                question_id=str(row.get("id", "")),
                question=question,
                gold=gold,
                sources=[name],
            )
            seen[question] = case
            cases.append(case)
        reports.append(report)
    # 合并去重后的来源信息写回每条 case
    for case in cases:
        case.sources = sorted(set(case.sources))
    return cases, reports


def recall(counts: list[bool]) -> tuple[int, int]:
    total = len(counts)
    hits = sum(1 for flag in counts)
    return hits, total


def main() -> int:
    settings = get_settings()
    articles, corpus_mode = load_corpus(settings.corpus_path)
    cases, source_reports = load_cases(articles)

    if not cases:
        message = "无标注数据，指标不可计算"
        print(message)
        OUT_MD.write_text(f"# 检索评测\n\n{message}\n", encoding="utf-8")
        return 0

    retriever = HybridRetriever(
        articles,
        settings.embedding_model,
        embedding_enabled=settings.embedding_enabled,
        embedding_device=settings.embedding_device,
        cache_dir=settings.embedding_cache_dir,
        reranker_model=settings.reranker_model,
        reranker_enabled=True,
        reranker_device=settings.reranker_device,
    )

    started = time.perf_counter()
    retriever._load_vectors()  # 先建索引，把加载/编码耗时与查询耗时分开记录
    index_seconds = round(time.perf_counter() - started, 2)
    mode = retriever.mode
    dense_available = retriever.vectors_available

    rerank_load_started = time.perf_counter()
    retriever.reranker.load()
    rerank_load_seconds = round(time.perf_counter() - rerank_load_started, 2)
    rerank_available = retriever.reranker.available
    rerank_reason = "" if rerank_available else retriever.reranker.reason

    encode_started = time.perf_counter()
    query_vectors = (
        retriever.encode_queries([case.question for case in cases])
        if dense_available
        else [None] * len(cases)
    )
    encode_seconds = round(time.perf_counter() - encode_started, 2)

    rows: list[dict] = []
    dense_hits: dict[int, list[bool]] = {5: [], 10: []}
    hybrid_hits: dict[int, list[bool]] = {5: [], 10: []}
    rerank_hits: dict[int, list[bool]] = {5: [], 10: []}
    doc_recall = {
        name: {5: [0, 0], 10: [0, 0]} for name in ("dense", "hybrid", "rerank")
    }
    timings = {"dense": 0.0, "hybrid": 0.0, "rerank": 0.0}

    for index, case in enumerate(cases):
        gold = case.gold
        query_vector = query_vectors[index]

        dense_top: list[tuple[LawArticle, float]] = []
        if dense_available:
            t0 = time.perf_counter()
            dense_top = retriever.search(case.question, 10, mode="dense", query_vector=query_vector)
            timings["dense"] += time.perf_counter() - t0

        t0 = time.perf_counter()
        hybrid_top = retriever.search(case.question, 10, mode="hybrid", query_vector=query_vector)
        timings["hybrid"] += time.perf_counter() - t0

        rerank_top: list[tuple[LawArticle, float]] = []
        if rerank_available:
            t0 = time.perf_counter()
            rerank_top = retriever.search_reranked(
                case.question, 10, candidate_k=20, query_vector=query_vector
            )
            timings["rerank"] += time.perf_counter() - t0

        def keys(items: list[tuple[LawArticle, float]]) -> list[tuple[str, str]]:
            return [(article.law_id, article.article_no) for article, _ in items]

        dense_keys = keys(dense_top)
        hybrid_keys = keys(hybrid_top)
        rerank_keys = keys(rerank_top)

        if dense_available:
            for k in (5, 10):
                dense_hits[k].append(bool(gold & set(dense_keys[:k])))
                doc_recall["dense"][k][0] += len(gold & set(dense_keys[:k]))
                doc_recall["dense"][k][1] += len(gold)
        for k in (5, 10):
            hybrid_hits[k].append(bool(gold & set(hybrid_keys[:k])))
            doc_recall["hybrid"][k][0] += len(gold & set(hybrid_keys[:k]))
            doc_recall["hybrid"][k][1] += len(gold)
        if rerank_available:
            for k in (5, 10):
                rerank_hits[k].append(bool(gold & set(rerank_keys[:k])))
                doc_recall["rerank"][k][0] += len(gold & set(rerank_keys[:k]))
                doc_recall["rerank"][k][1] += len(gold)

        rows.append(
            {
                "question_id": case.question_id,
                "sources": "+".join(case.sources),
                "question": case.question,
                "gold_count": len(gold),
                "gold_keys": "|".join(f"{law}#{no}" for law, no in sorted(gold)),
                "dense_hit5": int(any(gold & set(dense_keys[:5]))) if dense_keys else "",
                "dense_hit10": int(any(gold & set(dense_keys[:10]))) if dense_keys else "",
                "dense_top5": "|".join(f"{law}#{no}" for law, no in dense_keys[:5]),
                "hybrid_hit5": int(any(gold & set(hybrid_keys[:5]))),
                "hybrid_hit10": int(any(gold & set(hybrid_keys[:10]))),
                "hybrid_top5": "|".join(f"{law}#{no}" for law, no in hybrid_keys[:5]),
                "rerank_hit5": int(any(gold & set(rerank_keys[:5]))) if rerank_keys else "",
                "rerank_hit10": int(any(gold & set(rerank_keys[:10]))) if rerank_keys else "",
                "rerank_top5": "|".join(f"{law}#{no}" for law, no in rerank_keys[:5]),
            }
        )

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    total = len(cases)
    lines = [
        "# 检索评测（原始命中计数）",
        "",
        "> 语料：本地 1465 条统一法条（民法典/劳动法/劳动合同法）。",
        f"> 语料模式：{corpus_mode}；条数：{len(articles)}。",
        f"> 向量后端：{retriever.vector_backend or '无'}；检索模式：{mode}；"
        f"向量不可用原因：{retriever.vector_reason or '无'}。",
        f"> embedding 模型：{settings.embedding_model}；设备：{retriever.embedder.resolved_device or '未加载'}。",
        f"> reranker：{settings.reranker_model or '未配置'}；"
        f"设备：{retriever.reranker.resolved_device or '未加载'}；"
        f"不可用原因：{rerank_reason or '无'}。",
        f"> 索引构建耗时：{index_seconds}s；查询编码耗时：{encode_seconds}s。",
        "",
        "## 数据集对齐与剔除",
        "",
        "| 文件 | 行数 | 对齐纳入 | 剔除 | 空标注 | 条号无法对齐 | 文件缺失 | SHA256 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for report in source_reports:
        lines.append(
            f"| {report['source']} | {report['rows']} | {report['aligned']} | "
            f"{report['excluded']} | {report['excluded_reasons']['empty_expected']} | "
            f"{report['excluded_reasons']['unresolved_ref']} | {report['excluded_reasons']['missing_file']} | "
            f"`{report['sha256'] or '文件缺失'}` |"
        )
    lines += [
        "",
        f"合并按 question 去重后参与评测：**{total}** 条。",
        "",
        "> 外部数据集不在本仓库内；上表 SHA256 用于确认与他人运行的是同一份数据。"
        "`qa_test_cases.jsonl` 从 40 行扩到 120 行是外部数据集扩容，不是检索方案变更。",
        "",
        "## 三档检索原始命中",
        "",
        "> 问题命中 = top-k 中至少命中 1 条 gold 条号；条号召回 = top-k 覆盖的 gold 条号数 / gold 条号总数。",
        "",
        "| 档位 | 问题命中@5 | 问题命中率@5 | 条号召回@5 | 条号召回率@5 | 问题命中@10 | 问题命中率@10 | 条号召回@10 | 条号召回率@10 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    def ratio(hits: int, count: int) -> str:
        return f"{hits / count:.4f}" if count else "不可计算"

    for label, key, hits, available in (
        ("dense", "dense", dense_hits, dense_available),
        ("hybrid", "hybrid", hybrid_hits, True),
        ("hybrid+rerank", "rerank", rerank_hits, rerank_available),
    ):
        if not available:
            lines.append(f"| {label} | 不可计算 | 不可计算 | 不可计算 | 不可计算 | 不可计算 | 不可计算 | 不可计算 | 不可计算 |")
            continue
        h5, n5 = recall(hits[5])
        h10, n10 = recall(hits[10])
        d5, dn5 = doc_recall[key][5]
        d10, dn10 = doc_recall[key][10]
        lines.append(
            f"| {label} | {h5}/{n5} | {ratio(h5, n5)} | {d5}/{dn5} | {ratio(d5, dn5)} | "
            f"{h10}/{n10} | {ratio(h10, n10)} | {d10}/{dn10} | {ratio(d10, dn10)} |"
        )
    lines += [
        "",
        "## 加载与查询耗时",
        "",
        f"- embedding 索引构建/缓存读取：{index_seconds}s",
        f"- reranker 加载：{rerank_load_seconds}s",
        f"- 查询编码（{total} 条）：{encode_seconds}s",
        f"- dense 查询累计：{timings['dense']:.2f}s",
        f"- hybrid 查询累计：{timings['hybrid']:.2f}s",
        f"- hybrid+rerank 查询累计：{timings['rerank']:.2f}s",
        "",
        "> 说明：以上为 QA 标注对齐后的离线检索结果；标注本身来自外部既有评测集，"
        "不代表真实用户分布，也不构成本项目审查质量的结论。",
        f"> CSV: {OUT_CSV}",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
