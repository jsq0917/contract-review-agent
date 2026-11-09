from pathlib import Path

import fitz

from lexflow.config import ROOT
from lexflow.parse import parse_document
from lexflow.segment import segment_clauses


def test_clause_offsets_within_text_docx():
    parsed = parse_document(ROOT / "examples" / "sample_service_contract.docx")
    clauses = segment_clauses(parsed)
    assert len(clauses) >= 5
    for clause in clauses:
        assert 0 <= clause.char_start < clause.char_end <= len(parsed.full_text)
        assert parsed.full_text[clause.char_start : clause.char_end] == clause.text


def test_docx_has_no_fabricated_page_number_and_uses_paragraph_anchor():
    parsed = parse_document(ROOT / "examples" / "sample_service_contract.docx")
    clauses = segment_clauses(parsed)
    assert clauses
    for clause in clauses:
        assert clause.page_no is None
        assert clause.locator.startswith("§")
        assert clause.locator.endswith("]")
        assert f"chars {clause.char_start}:{clause.char_end}" in clause.locator
        assert clause.doc_id == parsed.doc_id


def test_docx_chapter_title_is_part_of_anchor(tmp_path: Path):
    from docx import Document

    doc = Document()
    doc.add_paragraph("第三章 违约责任")
    doc.add_paragraph("第十条 乙方逾期交货的，每逾期一日按合同总价千分之三支付违约金。")
    path = tmp_path / "chapter.docx"
    doc.save(path)

    parsed = parse_document(path)
    clauses = segment_clauses(parsed)
    assert clauses
    target = next(clause for clause in clauses if "第十条" in clause.text)
    assert target.page_no is None
    assert "第三章 违约责任" in target.locator
    assert target.locator.startswith("§")


def test_pdf_clause_page_and_bbox_are_from_real_layout():
    path = ROOT / "examples" / "sample_procurement_contract.pdf"
    parsed = parse_document(path)
    clauses = segment_clauses(parsed)
    assert clauses

    pdf = fitz.open(path)
    try:
        assert len(parsed.pages) == pdf.page_count
        for clause in clauses:
            assert clause.page_no is not None
            assert 1 <= clause.page_no <= pdf.page_count
            page = next(item for item in parsed.pages if item.page_no == clause.page_no)
            assert page.char_start <= clause.char_start < page.char_end
            if clause.bbox is not None:
                rect = pdf[clause.page_no - 1].rect
                assert rect.x0 <= clause.bbox.x0 <= clause.bbox.x1 <= rect.x1
                assert rect.y0 <= clause.bbox.y0 <= clause.bbox.y1 <= rect.y1
    finally:
        pdf.close()
