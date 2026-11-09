from pathlib import Path

from docx import Document

from lexflow.config import ROOT
from lexflow.service import AnalyzeService


def test_mock_end_to_end_docx_count_matches_db(service):
    result = service.analyze(
        ROOT / "examples" / "sample_service_contract.docx",
        engine="mock",
        as_of="2026-11-03",
        require_confirmation=False,
        auto_approve=True,
    )
    assert result.status == "completed"
    assert result.engine == "mock"
    assert result.findings_count > 0
    assert Path(result.docx_path or "").exists()
    assert service.store.count_findings(result.matter_id) == result.findings_count
    node_ms = result.report["metrics"]["node_latency_ms"]
    assert {"assemble_report", "export_docx"} <= set(node_ms)

    document = Document(result.docx_path)
    risk_table = document.tables[0]
    assert len(risk_table.rows) - 1 == result.findings_count
    headers = [cell.text.strip() for cell in risk_table.rows[0].cells]
    assert headers == ["条款", "定位锚", "风险等级", "问题", "原文", "法条依据", "建议"]
    for row in risk_table.rows[1:]:
        locator = row.cells[1].text
        assert "§" in locator
        assert "chars " in locator
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "engine=mock" in text or "engine: mock" in text
    assert "MOCK 演示" in text
    assert "doc_id:" in text


def test_interrupt_then_resume_completes(service):
    result = service.analyze(
        ROOT / "examples" / "sample_procurement_contract.pdf",
        engine="mock",
        as_of="2026-11-03",
        require_confirmation=True,
        auto_approve=False,
    )
    assert result.status == "awaiting_review"
    assert result.docx_path is None
    resumed = service.review(result.matter_id, action="approve", comment="pytest 确认")
    assert resumed.status == "completed"
    assert Path(resumed.docx_path or "").exists()
    assert service.store.count_findings(result.matter_id) == resumed.findings_count
    assert "export_docx" in resumed.report["metrics"]["node_latency_ms"]

    document = Document(resumed.docx_path)
    risk_table = document.tables[0]
    for row in risk_table.rows[1:]:
        locator = row.cells[1].text
        assert "p." in locator
        assert "¶" in locator
        assert "chars " in locator


def test_interrupt_resume_across_new_service_instance(settings):
    first = AnalyzeService(settings)
    result = first.analyze(
        ROOT / "examples" / "sample_procurement_contract.pdf",
        engine="mock",
        as_of="2026-11-03",
        require_confirmation=True,
        auto_approve=False,
    )
    assert result.status == "awaiting_review"

    # 新 service 实例只依赖同一个 SqliteSaver checkpoint，不共享第一次的内存状态。
    second = AnalyzeService(settings)
    resumed = second.review(result.matter_id, action="approve", comment="新实例恢复")
    assert resumed.status == "completed"
    assert Path(resumed.docx_path or "").exists()
    assert second.store.count_findings(result.matter_id) == resumed.findings_count


def test_reject_keeps_checkpoint_without_export(service):
    result = service.analyze(
        ROOT / "examples" / "sample_service_contract.docx",
        engine="mock",
        as_of="2026-11-03",
        require_confirmation=True,
        auto_approve=False,
    )
    assert result.status == "awaiting_review"
    rejected = service.review(result.matter_id, action="reject", comment="pytest 驳回")
    assert rejected.status == "rejected"
    assert rejected.docx_path == ""
    assert service.store.get_matter(result.matter_id)["status"] == "rejected"
