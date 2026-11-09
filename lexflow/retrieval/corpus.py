"""法条语料加载与内置最小样例。

外部语料 JSONL 每行字段：
law_id/law_name/article_no/chapter_path/text/effective_from/effective_to/source_url/version_id/content_hash
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

LAW_ID_BY_NAME = {
    "中华人民共和国民法典": "cc",
    "中华人民共和国劳动法": "ll",
    "中华人民共和国劳动合同法": "lcl",
}


@dataclass
class LawArticle:
    law_name: str
    article_no: str
    text: str
    effective_from: date | None
    effective_to: date | None
    source_url: str
    version_id: str
    law_id: str = ""
    chapter_path: str = ""
    content_hash: str = ""

    @property
    def key(self) -> str:
        return f"{self.law_name}#{self.article_no}"

    @property
    def chapter(self) -> str:
        """兼容旧代码的章节访问。"""
        return self.chapter_path


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# 仅当外部语料缺失时使用；README 会标注“样例集，非完整法律库”。
FALLBACK_ARTICLES = [
    {
        "article_no": "第五百零九条",
        "text": "当事人应当按照约定全面履行自己的义务。当事人应当遵循诚信原则，根据合同的性质、目的和交易习惯履行通知、协助、保密等义务。",
        "chapter_path": "第三编 合同 第一分编 通则 第四章 合同的履行",
    },
    {
        "article_no": "第五百一十条",
        "text": "合同生效后，当事人就质量、价款或者报酬、履行地点等内容没有约定或者约定不明确的，可以协议补充；不能达成补充协议的，按照合同相关条款或者交易习惯确定。",
        "chapter_path": "第三编 合同 第一分编 通则 第四章 合同的履行",
    },
    {
        "article_no": "第五百六十三条",
        "text": "有下列情形之一的，当事人可以解除合同：（一）因不可抗力致使不能实现合同目的；（二）在履行期限届满前，当事人一方明确表示或者以自己的行为表明不履行主要债务；（三）当事人一方迟延履行主要债务，经催告后在合理期限内仍未履行；（四）当事人一方迟延履行债务或者有其他违约行为致使不能实现合同目的；（五）法律规定的其他情形。",
        "chapter_path": "第三编 合同 第一分编 通则 第七章 合同的权利义务终止",
    },
    {
        "article_no": "第五百七十七条",
        "text": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。",
        "chapter_path": "第三编 合同 第一分编 通则 第八章 违约责任",
    },
    {
        "article_no": "第五百八十五条",
        "text": "当事人可以约定一方违约时应当根据违约情况向对方支付一定数额的违约金，也可以约定因违约产生的损失赔偿额的计算方法。约定的违约金低于造成的损失的，人民法院或者仲裁机构可以根据当事人的请求予以增加；约定的违约金过分高于造成的损失的，人民法院或者仲裁机构可以根据当事人的请求予以适当减少。",
        "chapter_path": "第三编 合同 第一分编 通则 第八章 违约责任",
    },
]


def load_corpus(path: str | Path) -> tuple[list[LawArticle], str]:
    """返回 (法条列表, 语料模式)。模式为 external/fallback。"""
    path = Path(path)
    if path.exists():
        articles: list[LawArticle] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                effective_from_text = row.get("effective_from") or ""
                effective_to_text = row.get("effective_to") or ""
                text = row["text"]
                articles.append(
                    LawArticle(
                        law_name=row["law_name"],
                        article_no=row["article_no"],
                        text=text,
                        effective_from=(
                            date.fromisoformat(effective_from_text) if effective_from_text else None
                        ),
                        effective_to=(
                            date.fromisoformat(effective_to_text) if effective_to_text else None
                        ),
                        source_url=row.get("source_url") or "",
                        version_id=row.get("version_id") or "",
                        law_id=row.get("law_id") or LAW_ID_BY_NAME.get(row["law_name"], ""),
                        chapter_path=row.get("chapter_path") or row.get("chapter") or "",
                        content_hash=row.get("content_hash") or _content_hash(text),
                    )
                )
        if articles:
            return articles, "external"
    law_name = "中华人民共和国民法典"
    articles = [
        LawArticle(
            law_name=law_name,
            article_no=row["article_no"],
            text=row["text"],
            effective_from=date(2021, 1, 1),
            effective_to=None,
            source_url="https://flk.npc.gov.cn/",
            version_id="cc-2021",
            law_id="cc",
            chapter_path=row["chapter_path"],
            content_hash=_content_hash(row["text"]),
        )
        for row in FALLBACK_ARTICLES
    ]
    return articles, "fallback"
