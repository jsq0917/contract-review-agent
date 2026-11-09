"""模型/规则输出的结构校验。

任何非法 JSON、缺字段、类型错误或引用落空都不允许被静默接受或补默认值，
由调用方写入 failures.jsonl 并跳过该条款结果。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class FindingDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_level: Literal["high", "medium", "low"]
    issue: str = Field(min_length=1)
    suggestion: str = Field(min_length=1)
    original_quote: str = Field(min_length=1)
    law_name: str | None = None
    article_no: str | None = None
    cited_quote: str | None = None
    rule_id: str | None = None
    risk_type: str | None = None
    citation_reason: str | None = None


class ReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clause_id: str | None = None
    findings: list[FindingDraft]


class SchemaViolation(ValueError):
    category = "schema_invalid"


class CitationNotFound(SchemaViolation):
    category = "citation_not_found"


def validate_payload(payload: Any) -> ReviewPayload:
    """Pydantic 校验；缺 findings、缺必填字段、额外字段都会抛 SchemaViolation。"""
    try:
        return ReviewPayload.model_validate(payload)
    except ValidationError as exc:
        raise SchemaViolation(str(exc)) from exc


def validate_citations(
    payload: ReviewPayload,
    known_articles: set[tuple[str, str]],
) -> None:
    """引用要么不出现，要么法名+条号成对且真实存在于语料。

    不允许只给法名或只给条号，也不允许引用语料中不存在的条号。
    """
    for draft in payload.findings:
        has_law = bool(draft.law_name)
        has_article = bool(draft.article_no)
        if not has_law and not has_article:
            continue
        if has_law != has_article:
            raise CitationNotFound("法条引用必须同时提供 law_name 与 article_no")
        pair = (str(draft.law_name), str(draft.article_no))
        if pair not in known_articles:
            raise CitationNotFound(f"引用落空：{pair[0]} {pair[1]} 不在语料中")
