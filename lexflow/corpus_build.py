r"""构建统一法条语料（民法典 1260 + 劳动法 107 + 劳动合同法 98 = 1465）。

数据来源全部在 `E:\project` 内：
- 民法典/劳动合同法正文：LexSynergy 结构化语料（由本地 Markdown 抽取）
- 劳动法正文：本地 Markdown 原文，逐条做康熙部首归一化

元数据只填写源文件可推断的内容；推断不了的留空，并在 manifest 与 README 说明。
构建过程不写时间戳，输出字节可复现。
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

REF_CORPUS = Path(r"E:\project\LexSynergy Legal QA System\data\corpus\articles.jsonl")
CIVIL_MD = Path(r"E:\project\LawAnswer-doubao\data\raw\中华人民共和国民法典.md")
LABOR_MD = Path(
    r"E:\project\LawAnswer-doubao\data\raw\中华人民共和国劳动法_中华人民共和国人力资源和社会保障部.md"
)
LCL_MD = Path(r"E:\project\LawAnswer-doubao\data\raw\中华人民共和国劳动合同法.md")

LAW_META = {
    "cc": {
        "law_name": "中华人民共和国民法典",
        "version_id": "cc-2021",
        "effective_from": "2021-01-01",
    },
    "ll": {
        "law_name": "中华人民共和国劳动法",
        "version_id": "ll-2018",
        "effective_from": "1995-01-01",
    },
    "lcl": {
        "law_name": "中华人民共和国劳动合同法",
        "version_id": "lcl-2008",
        "effective_from": "2008-01-01",
    },
}
LAW_ORDER = ("cc", "ll", "lcl")
EXPECTED_COUNTS = {"cc": 1260, "ll": 107, "lcl": 98}

ARTICLE_RE = re.compile(r"^第[一二三四五六七八九十百千零]+条(?=[\u3000 \t]|$)")
RAW_ARTICLE_PREFIX_RE = re.compile(r"^第[一二三四五六七八九十百千零\u2E80-\u2FDF]+条")
CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十]+章")
FOOTER_RE = re.compile(r"^(网站声明|主办单位|京ICP|网站标识码|联系我们|网站地图)")
NOISE_RE = re.compile(r"^(\d+/\d+|\d{4}/\d{1,2}/\d{1,2})")

# CJK 部首补充区（U+2E80–U+2EFF）NFKC 不会映射，按源文件中实际出现的 4 个字显式映射。
RADICAL_SUPPLEMENT_FIXES = {
    "⺠": "民",  # U+2EA0
    "⻔": "门",  # U+2ED4
    "⻓": "长",  # U+2ED3
    "⻅": "见",  # U+2EC5
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_kangxi(text: str) -> tuple[str, int]:
    """把康熙部首/CJK 部首补充区字符归一为常用汉字，其他字符原样保留。"""
    output: list[str] = []
    replaced = 0
    for char in text:
        code = ord(char)
        mapped = RADICAL_SUPPLEMENT_FIXES.get(char)
        if mapped:
            output.append(mapped)
            replaced += 1
            continue
        if 0x2F00 <= code <= 0x2FDF:
            normalized = unicodedata.normalize("NFKC", char)
            if normalized != char:
                output.append(normalized)
                replaced += 1
                continue
        output.append(char)
    return "".join(output), replaced


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _tidy_chapter(text: str) -> str:
    return re.sub(r"[\u3000 \t]+", " ", text.strip())


def _clean_ref_text(law_id: str, article_no: str, text: str) -> str:
    """清理参考语料最后一条混入的网页页脚片段；其余文本不动。"""
    text = text.strip()
    if law_id == "cc" and article_no == "第一千二百六十条":
        text = text.split("新华社")[0].strip()
    if law_id == "lcl" and article_no == "第九十八条":
        text = text.split("八次会议通过）")[0].strip()
    return text


def _extract_labor(md_text: str) -> tuple[list[dict], dict]:
    """从劳动法 Markdown 抽取 107 条，并保留归一化前后的正文。"""
    raw_lines = md_text.splitlines()
    norm_lines: list[str] = []
    per_line_replaced: list[int] = []
    for line in raw_lines:
        normalized, count = normalize_kangxi(line)
        norm_lines.append(normalized)
        per_line_replaced.append(count)

    first_index = next(
        (index for index, line in enumerate(norm_lines) if ARTICLE_RE.match(line.strip("\u3000 \t"))),
        None,
    )
    if first_index is None:
        raise ValueError("劳动法 Markdown 中未找到条号")

    current_chapter = ""
    for index in range(first_index):
        stripped = _tidy_chapter(norm_lines[index])
        if CHAPTER_RE.match(stripped):
            current_chapter = stripped

    articles: list[dict] = []
    current: dict | None = None
    before_parts: list[str] = []
    after_parts: list[str] = []

    def flush() -> None:
        nonlocal current
        if current is not None:
            current["text_before"] = "".join(before_parts).strip()
            current["text"] = "".join(after_parts).strip()
            current["chars_replaced"] = sum(current.pop("_line_counts"))
            articles.append(current)
            current = None

    for index in range(first_index, len(raw_lines)):
        raw_line = raw_lines[index]
        norm_line = norm_lines[index]
        stripped = norm_line.strip("\u3000 \t")
        raw_stripped = raw_line.strip("\u3000 \t")
        if not stripped or NOISE_RE.match(stripped) or FOOTER_RE.match(stripped):
            continue
        if stripped.startswith("http://") or stripped.startswith("https://"):
            continue
        if CHAPTER_RE.match(stripped):
            flush()
            current_chapter = _tidy_chapter(stripped)
            continue
        heading = ARTICLE_RE.match(stripped)
        if heading:
            flush()
            before_parts = []
            after_parts = []
            article_no = heading.group(0)
            current = {
                "article_no": article_no,
                "chapter_path": current_chapter,
                "_line_counts": [per_line_replaced[index]],
            }
            after_body = stripped[len(article_no) :].strip("\u3000 \t")
            raw_match = RAW_ARTICLE_PREFIX_RE.match(raw_stripped)
            before_body = raw_stripped[raw_match.end() :].strip("\u3000 \t") if raw_match else raw_stripped
            if after_body:
                after_parts.append(after_body)
                before_parts.append(before_body)
            continue
        if current is not None and not FOOTER_RE.match(stripped):
            after_parts.append(stripped)
            before_parts.append(raw_stripped)
            current["_line_counts"].append(per_line_replaced[index])
    flush()

    if len(articles) != EXPECTED_COUNTS["ll"]:
        raise ValueError(f"劳动法条数不符合预期: {len(articles)} != {EXPECTED_COUNTS['ll']}")
    if articles[0]["article_no"] != "第一条" or articles[-1]["article_no"] != "第一百零七条":
        raise ValueError("劳动法条号边界不符合预期")

    summary = {
        "source_chars": sum(1 for char in md_text if 0x2E80 <= ord(char) <= 0x2FDF),
        "article_chars": sum(article["chars_replaced"] for article in articles),
        "affected_articles": sum(1 for article in articles if article["text_before"] != article["text"]),
    }
    return articles, summary


def _verify_phrase(text: str, pattern: str, label: str) -> None:
    if not re.search(pattern, text):
        raise ValueError(f"{label} 的生效日期未在源文件中找到，拒绝填写")


def _find_source_url(pattern: str, text: str, *, add_scheme: bool = False) -> str:
    match = re.search(pattern, text)
    if not match:
        return ""
    url = match.group(0)
    if add_scheme and url.startswith("www."):
        url = "https://" + url
    return unicodedata.normalize("NFKC", url)


def build_corpus(
    out_dir: str | Path,
    *,
    ref_corpus: Path = REF_CORPUS,
    civil_md: Path = CIVIL_MD,
    labor_md: Path = LABOR_MD,
    lcl_md: Path = LCL_MD,
) -> dict:
    """构建 1465 条统一语料；返回 manifest 字典。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    civil_text = civil_md.read_text(encoding="utf-8")
    labor_text = labor_md.read_text(encoding="utf-8")
    lcl_text = lcl_md.read_text(encoding="utf-8")

    _verify_phrase(civil_text, r"自\s*2021\s*年\s*1\s*月\s*1\s*日起施行", "民法典")
    _verify_phrase(lcl_text, r"自\s*2008\s*年\s*1\s*月\s*1\s*日起施行", "劳动合同法")
    _verify_phrase(
        normalize_kangxi(labor_text)[0], r"自\s*1995\s*年\s*1\s*月\s*1\s*日起施行", "劳动法"
    )

    source_urls = {
        "cc": _find_source_url(r"www\.npc\.gov\.cn[^\s）]+", civil_text, add_scheme=True),
        "ll": _find_source_url(r"https://www\.mohrss\.gov\.cn[^\s）]+", labor_text),
        "lcl": "",
    }

    ref_rows = _read_jsonl(ref_corpus)
    by_law: dict[str, list[dict]] = {law_id: [] for law_id in LAW_ORDER}
    for row in ref_rows:
        law_id = "cc" if row["law_name"] == LAW_META["cc"]["law_name"] else None
        if row["law_name"] == LAW_META["ll"]["law_name"]:
            law_id = "ll"
        if row["law_name"] == LAW_META["lcl"]["law_name"]:
            law_id = "lcl"
        if law_id is None:
            continue
        by_law[law_id].append(row)

    if len(by_law["cc"]) != EXPECTED_COUNTS["cc"]:
        raise ValueError(f"民法典条数不符合预期: {len(by_law['cc'])}")
    if len(by_law["lcl"]) != EXPECTED_COUNTS["lcl"]:
        raise ValueError(f"劳动合同法条数不符合预期: {len(by_law['lcl'])}")

    articles: list[dict] = []
    normalization_records: list[dict] = []

    for law_id in ("cc", "lcl"):
        meta = LAW_META[law_id]
        for row in by_law[law_id]:
            text = _clean_ref_text(law_id, row["article_no"], row["content"])
            text, replaced = normalize_kangxi(text)
            articles.append(
                {
                    "law_id": law_id,
                    "law_name": meta["law_name"],
                    "article_no": row["article_no"],
                    "chapter_path": row.get("chapter_path", ""),
                    "text": text,
                    "effective_from": meta["effective_from"],
                    "effective_to": "",
                    "source_url": source_urls[law_id],
                    "version_id": meta["version_id"],
                    "content_hash": sha256_text(text),
                }
            )
            if replaced:
                raise ValueError(f"{law_id} {row['article_no']} 仍含未被记录的部首字符")

    labor_articles, labor_summary = _extract_labor(labor_text)
    meta = LAW_META["ll"]
    for article in labor_articles:
        text = article["text"]
        articles.append(
            {
                "law_id": "ll",
                "law_name": meta["law_name"],
                "article_no": article["article_no"],
                "chapter_path": article["chapter_path"],
                "text": text,
                "effective_from": meta["effective_from"],
                "effective_to": "",
                "source_url": source_urls["ll"],
                "version_id": meta["version_id"],
                "content_hash": sha256_text(text),
            }
        )
        if article["text_before"] != text:
            normalization_records.append(
                {
                    "law_id": "ll",
                    "law_name": meta["law_name"],
                    "article_no": article["article_no"],
                    "chars_replaced": article["chars_replaced"],
                    "text_before": article["text_before"],
                    "text_after": text,
                }
            )

    counts = {law_id: sum(1 for item in articles if item["law_id"] == law_id) for law_id in LAW_ORDER}
    for law_id, expected in EXPECTED_COUNTS.items():
        if counts[law_id] != expected:
            raise ValueError(f"{law_id} 条数不符合预期: {counts[law_id]} != {expected}")
    keys = {(item["law_id"], item["article_no"]) for item in articles}
    if len(keys) != len(articles):
        raise ValueError("统一语料存在重复 (law_id, article_no)")

    corpus_path = out_dir / "law_articles.jsonl"
    with corpus_path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in articles:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    normalization_path = out_dir / "labor_law_normalization.jsonl"
    with normalization_path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in normalization_records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    manifest = {
        "builder_version": "corpus-v2",
        "total": len(articles),
        "by_law": [
            {
                "law_id": law_id,
                "law_name": LAW_META[law_id]["law_name"],
                "count": counts[law_id],
                "effective_from": LAW_META[law_id]["effective_from"],
                "source_url": source_urls[law_id],
            }
            for law_id in LAW_ORDER
        ],
        "sources": {
            "ref_corpus": str(ref_corpus),
            "civil_md": str(civil_md),
            "labor_md": str(labor_md),
            "lcl_md": str(lcl_md),
        },
        "normalization": {
            "law_id": "ll",
            "source_chars": labor_summary["source_chars"],
            "article_chars": labor_summary["article_chars"],
            "affected_articles": labor_summary["affected_articles"],
            "log_file": "labor_law_normalization.jsonl",
            "supplement_fixes": RADICAL_SUPPLEMENT_FIXES,
        },
        "text_cleaning": [
            "民法典第一千二百六十条：移除源语料尾部“新华社…”网页片段",
            "劳动合同法第九十八条：移除源语料尾部“八次会议通过）”片段",
        ],
        "missing_metadata": [
            {
                "field": "effective_to",
                "scope": "all",
                "reason": "源文件未提供废止/失效日期，全部留空",
            },
            {
                "field": "source_url",
                "scope": "lcl",
                "reason": "本地劳动合同法 Markdown 未包含来源 URL",
            },
            {
                "field": "effective_from(修正生效日)",
                "scope": "ll",
                "reason": "仅第一百零七条明示 1995-01-01；2018 修正案的生效日源文件未明示",
            },
        ],
        "files": {
            "law_articles.jsonl": sha256_file(corpus_path),
            "labor_law_normalization.jsonl": sha256_file(normalization_path),
        },
    }
    (out_dir / "corpus_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest
