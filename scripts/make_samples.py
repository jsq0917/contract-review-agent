"""生成两份自造示例合同：一份 docx、一份 PDF。

性质：本项目自造样例，仅用于链路测试，不是真实合同，也不构成法律文本范本。
"""

from __future__ import annotations

from pathlib import Path

from docx import Document

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "examples"

SERVICE_TEXT = """服务合同（示例）

甲方：某科技有限公司（示例主体）
乙方：某咨询有限公司（示例主体）

第一条 服务内容
乙方为甲方提供合同流程咨询服务，具体交付物以附件一为准。

第二条 服务费用及付款
服务费总额为人民币十万元。甲方应在合同签订后五个工作日内支付全款，乙方未完成验收前甲方不得要求退款。

第三条 验收
甲方应在收到交付物后三日内完成验收，逾期未提出异议视为验收合格。

第四条 违约责任
任何一方违约的，应按合同总额每日千分之五向对方支付违约金，违约金不设上限。

第五条 单方解除
甲方有权在任何时候无理由单方解除本合同，且无需承担任何赔偿责任。

第六条 争议解决
因本合同产生的争议，由甲方所在地人民法院管辖。

第七条 保密
双方应对履约过程中知悉的商业秘密承担保密义务，保密期限为合同终止后两年。
"""

PROCUREMENT_TEXT = """采购合同（示例）

甲方：某制造有限公司（示例主体）
乙方：某设备有限公司（示例主体）

第一条 采购标的
乙方向甲方供应生产线设备一套，型号与数量见附件清单。

第二条 合同价款与支付
合同总价为人民币二百万元。甲方应在合同生效后十日内支付百分之百预付款，乙方发货时间另行协商。

第三条 交付与验收
乙方应在收到预付款后一百二十日内交付。甲方应在到货后七日内验收，验收标准由乙方单方确定。

第四条 质量保证
设备质保期为验收合格后十二个月，质保期内非人为损坏由乙方免费维修。

第五条 违约责任
乙方逾期交货的，每逾期一日按合同总价千分之三支付违约金；甲方逾期付款的，不承担违约责任。

第六条 解除
乙方交付的设备经两次维修仍不能正常使用的，甲方有权解除合同并要求退还全部价款。

第七条 争议解决
争议由乙方所在地人民法院管辖。
"""


def make_docx(path: Path) -> None:
    doc = Document()
    for line in SERVICE_TEXT.strip().splitlines():
        if line.strip():
            doc.add_paragraph(line.strip())
    doc.save(path)


def make_pdf(path: Path) -> None:
    import pymupdf as fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # 使用 PyMuPDF 内置简体中文字体，避免依赖系统字体。
    page.insert_textbox(
        fitz.Rect(50, 50, 545, 800),
        PROCUREMENT_TEXT.strip(),
        fontsize=11,
        fontname="china-s",
        lineheight=1.5,
    )
    doc.save(path)
    doc.close()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    make_docx(OUT / "sample_service_contract.docx")
    make_pdf(OUT / "sample_procurement_contract.pdf")
    print("示例合同已生成：")
    for path in sorted(OUT.iterdir()):
        print(f"- {path.name} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
