"""请求级 trace：事件完整、trace_id 一致、密钥与长正文不落盘。"""

from __future__ import annotations

from pathlib import Path

from lexflow.config import ROOT
from lexflow.trace import Tracer, read_trace, sanitize


def test_trace_covers_whole_request_and_survives_resume(service, settings):
    result = service.analyze(
        ROOT / "examples" / "sample_service_contract.docx",
        engine="mock",
        as_of="2026-11-03",
        require_confirmation=True,
        auto_approve=False,
    )
    assert result.status == "awaiting_review"
    trace_path = settings.artifacts_dir / result.matter_id / "trace.jsonl"
    events = read_trace(trace_path)
    names = [item["event"] for item in events]

    assert names[0] == "trace.start"
    assert "request.start" in names
    assert "request.end" in names
    for node in ("parse", "segment_clauses", "review_legal", "review_commercial", "bind_evidence", "verify", "assemble_report"):
        assert any(item["event"] == "node.end" and item.get("node") == node for item in events), node
    assert all(item["trace_id"] == result.trace_id for item in events)
    assert result.report["metrics"]["trace_id"] == result.trace_id
    assert result.report["metrics"]["prompt_version"]
    assert result.report["metrics"]["corpus_version"] != "unknown"

    resumed = service.review(result.matter_id, action="approve", comment="trace 测试确认")
    events = read_trace(trace_path)
    names = [item["event"] for item in events]
    assert "request.resume" in names
    hitl = [item for item in events if item["event"] == "hitl.decision"]
    assert hitl and hitl[-1]["exported"] is True
    assert hitl[-1]["output"] == "report.docx"
    assert resumed.trace_id == result.trace_id


def test_trace_redacts_secrets_and_truncates_long_text(tmp_path: Path):
    path = tmp_path / "trace.jsonl"
    tracer = Tracer(
        "trace-test",
        path,
        meta={"api_key": "sk-abcdefghijklmnop", "note": "Authorization: Bearer sk-realvalue12345678"},
    )
    tracer.record("llm.call", prompt="x" * 5000, api_key="sk-should-not-appear")
    raw = path.read_text(encoding="utf-8")
    assert "sk-abcdefghijklmnop" not in raw
    assert "sk-realvalue12345678" not in raw
    assert "sk-should-not-appear" not in raw
    events = read_trace(path)
    prompt = events[-1]["prompt"]
    assert "truncated:5000" in prompt
    assert len(prompt) < 400


def test_sanitize_keeps_counts_out_of_truncation():
    assert sanitize({"chars": 12000}) == {"chars": 12000}
    assert sanitize({"text": "a" * 400})["text"].endswith("truncated:400>")
