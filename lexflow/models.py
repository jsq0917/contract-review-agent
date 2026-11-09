"""全链路 Pydantic 模型。字段命名尽量直白，便于报告与评测复用。"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

RiskLevel = Literal["high", "medium", "low"]
GateName = Literal["G1", "G2", "G3", "G4", "G5"]


class BBox(BaseModel):
    """PyMuPDF 坐标（左上原点，单位 pt）。"""

    x0: float
    y0: float
    x1: float
    y1: float


class PageBlock(BaseModel):
    text: str
    bbox: BBox | None = None
    char_start: int
    char_end: int
    anchor: str = ""
    page_no: int | None = None


class ParsedPage(BaseModel):
    page_no: int
    text: str
    char_start: int
    char_end: int
    blocks: list[PageBlock] = Field(default_factory=list)


class ParsedDocument(BaseModel):
    doc_id: str
    source_path: str
    source_type: Literal["pdf", "docx", "image"]
    full_text: str
    pages: list[ParsedPage] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Clause(BaseModel):
    clause_id: str
    index: int
    text: str
    page_no: int | None = None
    bbox: BBox | None = None
    char_start: int
    char_end: int
    clause_type: str = "unknown"
    doc_id: str = ""
    locator: str = ""


class Evidence(BaseModel):
    evidence_id: str
    law_name: str
    article_no: str
    quote: str
    effective_from: date
    effective_to: date | None = None
    source_url: str
    version_id: str
    score: float = 0.0


class Finding(BaseModel):
    finding_id: str
    clause_id: str
    reviewer: Literal["legal", "commercial"]
    risk_level: RiskLevel
    issue: str
    suggestion: str
    original_quote: str
    cited_quote: str = ""
    law_name: str | None = None
    article_no: str | None = None
    evidence_id: str | None = None
    rule_id: str | None = None
    risk_type: str = ""
    reviewers: list[str] = Field(default_factory=list)
    citation_reason: str | None = None
    engine: str = "mock"
    doc_id: str = ""
    locator: str = ""


class Verification(BaseModel):
    finding_id: str
    gate: GateName
    passed: bool
    detail: str


class HumanDecision(BaseModel):
    action: Literal["approve", "modify", "reject"]
    comment: str = ""
    decided_at: str


class DoucmentMetrics(BaseModel):
    """成本与耗时原始计数；不做任何效果判断。"""

    started_at: str
    elapsed_ms: int = 0
    node_latency_ms: dict[str, int] = Field(default_factory=dict)
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_estimate_cny: float | None = None
    retrieval_mode: str = "bm25-only"


class Report(BaseModel):
    matter_id: str
    engine: str
    stance: str
    as_of: date
    doc_id: str = ""
    findings: list[Finding]
    evidence: list[Evidence]
    verifications: list[Verification]
    warnings: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    disclaimer: str = "本报告由机器生成，engine 标注为 mock 时仅为链路演示，不构成法律意见。"


class LLMResponse(BaseModel):
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
