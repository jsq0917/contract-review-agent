"""docxtpl 导出：模板缺失或旧版列结构时自动重建；风险表行数必须与 findings 一致。"""

from __future__ import annotations

from pathlib import Path

from lexflow.models import Report

HEADERS = ["条款", "定位锚", "风险等级", "问题", "原文", "法条依据", "建议"]


def _template_is_current(path: Path) -> bool:
    try:
        from docx import Document

        doc = Document(path)
        if not doc.tables:
            return False
        return [cell.text.strip() for cell in doc.tables[0].rows[0].cells] == HEADERS
    except Exception:
        return False


def ensure_template(path: str | Path) -> Path:
    path = Path(path)
    if path.exists() and _template_is_current(path):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    from docx import Document

    doc = Document()
    doc.add_heading("合同审查报告（M0）", level=1)
    doc.add_paragraph("matter_id: {{ matter_id }}")
    doc.add_paragraph("doc_id: {{ doc_id }}")
    doc.add_paragraph("engine: {{ engine }}")
    doc.add_paragraph("审查立场: {{ stance }}；法律时点: {{ as_of }}")
    doc.add_paragraph("{% if is_mock %}【MOCK 演示】本报告由离线 stub 生成，仅验证链路，不构成法律意见。{% endif %}")
    doc.add_paragraph("风险条款清单（定位锚 = doc_id · 页码/段落 · 字符 span）")
    table = doc.add_table(rows=1, cols=len(HEADERS))
    table.style = "Table Grid"
    for cell, text in zip(table.rows[0].cells, HEADERS):
        cell.text = text
    loop_row = table.add_row()
    loop_row.cells[0].text = "{%tr for f in findings %}"
    row = table.add_row()
    values = [
        "{{ f.clause_id }}",
        "{{ f.locator_display }}",
        "{{ f.risk_level }}",
        "{{ f.issue }}",
        "{{ f.original_quote }}",
        "{{ f.law_display }}",
        "{{ f.suggestion }}",
    ]
    for cell, value in zip(row.cells, values):
        cell.text = value
    end_row = table.add_row()
    end_row.cells[0].text = "{%tr endfor %}"

    doc.add_heading("核验附录", level=2)
    doc.add_paragraph("G1 条号存在；G2 逐字引文；G3 as_of 时效。核验结果见下表。")
    vtable = doc.add_table(rows=1, cols=3)
    vtable.style = "Table Grid"
    for cell, text in zip(vtable.rows[0].cells, ["风险项", "闸门", "结果"]):
        cell.text = text
    vloop = vtable.add_row()
    vloop.cells[0].text = "{%tr for v in verifications %}"
    vrow = vtable.add_row()
    vrow.cells[0].text = "{{ v.finding_id }}"
    vrow.cells[1].text = "{{ v.gate }}"
    vrow.cells[2].text = "{{ v.status|upper }}: {{ v.detail }}"
    vend = vtable.add_row()
    vend.cells[0].text = "{%tr endfor %}"
    doc.save(path)
    return path


def export_docx(report: Report, template_path: str | Path, output_path: str | Path) -> Path:
    from docxtpl import DocxTemplate

    template_path = ensure_template(template_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    findings = []
    for finding in report.findings:
        if finding.law_name and finding.article_no:
            law_display = f"{finding.law_name} {finding.article_no}"
        else:
            law_display = f"未指定：{finding.citation_reason or '原因未提供'}"
        findings.append(
            {
                "clause_id": finding.clause_id,
                "locator_display": " · ".join(
                    part for part in (finding.doc_id, finding.locator) if part
                )
                or "无定位",
                "risk_level": finding.risk_level,
                "issue": finding.issue,
                "original_quote": finding.original_quote,
                "law_display": law_display,
                "suggestion": finding.suggestion,
            }
        )
    verifications = [
        {
            "finding_id": item.finding_id,
            "gate": item.gate,
            "passed": item.passed,
            "status": item.status,
            "detail": item.detail,
        }
        for item in report.verifications
    ]
    context = {
        "matter_id": report.matter_id,
        "doc_id": report.doc_id,
        "engine": report.engine,
        "is_mock": report.engine == "mock",
        "stance": report.stance,
        "as_of": report.as_of.isoformat(),
        "findings": findings,
        "verifications": verifications,
    }
    doc = DocxTemplate(str(template_path))
    doc.render(context)
    doc.save(str(output_path))
    return output_path
