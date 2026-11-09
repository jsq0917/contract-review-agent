"""条款切分：基于行首编号的规则切分，保留原文字符偏移与页码。"""

from __future__ import annotations

import re

from lexflow.models import BBox, Clause, ParsedDocument

ARTICLE_RE = re.compile(r"(?m)^[ \t]*(第[一二三四五六七八九十百千零]+条)[ \t、.．:：]*")
NUMBER_RE = re.compile(r"(?m)^[ \t]*(\d{1,3})[.、．][ \t]*")
CN_NUM_RE = re.compile(r"(?m)^[ \t]*([一二三四五六七八九十]+)、")

# 顺序即并列时的优先级：解除/违约等“强标签”排在通用付款之前。
TYPE_KEYWORDS = [
    ("termination", ["单方解除", "解除", "终止", "退租", "退出"]),
    ("liability", ["违约金", "违约", "赔偿", "损失"]),
    ("jurisdiction", ["管辖", "仲裁", "诉讼", "争议解决"]),
    ("confidentiality", ["保密", "商业秘密", "秘密"]),
    ("force_majeure", ["不可抗力", "免责"]),
    ("payment", ["付款", "价款", "支付", "结算", "发票"]),
    ("delivery", ["交付", "验收", "履行期限", "交货"]),
]


def infer_clause_type(text: str) -> str:
    """按关键词命中次数打分，次数相同按 TYPE_KEYWORDS 优先级。"""
    best_label = "general"
    best_score = 0
    for label, keywords in TYPE_KEYWORDS:
        score = sum(text.count(keyword) for keyword in keywords)
        if score > best_score:
            best_label = label
            best_score = score
    return best_label


def _page_number(doc: ParsedDocument, page_no: int) -> int | None:
    # docx 没有真实分页，不能伪造页码；PDF/图片的单页版面才是真实页号。
    return page_no if doc.source_type in {"pdf", "image"} else None


def _locate(doc: ParsedDocument, char_start: int) -> tuple[int | None, BBox | None, str]:
    for page in doc.pages:
        if page.char_start <= char_start <= page.char_end:
            for block in page.blocks:
                if block.char_start <= char_start < block.char_end:
                    return _page_number(doc, page.page_no), block.bbox, block.anchor
            anchor = f"p.{page.page_no}" if doc.source_type in {"pdf", "image"} else ""
            return _page_number(doc, page.page_no), None, anchor
    if doc.pages:
        page = doc.pages[-1]
        anchor = f"p.{page.page_no}" if doc.source_type in {"pdf", "image"} else ""
        return _page_number(doc, page.page_no), None, anchor
    return None, None, ""


def _trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _boundaries(text: str) -> list[int]:
    positions: list[int] = []
    for regex in (ARTICLE_RE, NUMBER_RE, CN_NUM_RE):
        positions.extend(match.start() for match in regex.finditer(text))
    return sorted(set(positions))


def segment_clauses(doc: ParsedDocument) -> list[Clause]:
    text = doc.full_text
    starts = _boundaries(text)
    if not starts:
        # 无编号时按空行切段，仍保留位置。
        starts = [match.start() for match in re.finditer(r"(?m)^[ \t]*\S", text)]
        if len(starts) <= 1:
            starts = [0]

    clauses: list[Clause] = []
    for index, raw_start in enumerate(starts):
        raw_end = starts[index + 1] if index + 1 < len(starts) else len(text)
        start, end = _trim_span(text, raw_start, raw_end)
        if end <= start:
            continue
        page_no, bbox, anchor = _locate(doc, start)
        locator = f"{anchor} [chars {start}:{end}]" if anchor else f"[chars {start}:{end}]"
        clauses.append(
            Clause(
                clause_id=f"C{len(clauses) + 1:04d}",
                index=len(clauses),
                text=text[start:end],
                page_no=page_no,
                bbox=bbox,
                char_start=start,
                char_end=end,
                clause_type=infer_clause_type(text[start:end]),
                doc_id=doc.doc_id,
                locator=locator,
            )
        )
    return clauses
