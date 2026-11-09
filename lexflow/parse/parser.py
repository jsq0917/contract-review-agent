"""解析入口：保留 page/bbox/char_offset，表格单独收集。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from lexflow.models import BBox, PageBlock, ParsedDocument, ParsedPage
from lexflow.parse.errors import OCRDependencyError, OCRRequiredError, UnsupportedFormatError

_CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百零〇]+章")


def _doc_id(path: Path, raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:16]


def parse_document(path: str | Path) -> ParsedDocument:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    raw = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _parse_pdf(path, raw)
    if suffix == ".docx":
        return _parse_docx(path, raw)
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        return _parse_image(path, raw)
    raise UnsupportedFormatError(f"M0 只支持 PDF/docx/图片，收到: {suffix}")


def _parse_pdf(path: Path, raw: bytes) -> ParsedDocument:
    import pymupdf as fitz

    doc = fitz.open(path)
    pages: list[ParsedPage] = []
    full_parts: list[str] = []
    cursor = 0
    for page_index, page in enumerate(doc, start=1):
        page_text_parts: list[str] = []
        blocks: list[PageBlock] = []
        block_index = 0
        for block in page.get_text("blocks"):
            x0, y0, x1, y1, text = block[0], block[1], block[2], block[3], block[4]
            text = str(text)
            if not text.strip():
                continue
            block_index += 1
            start = cursor + sum(len(p) for p in page_text_parts)
            page_text_parts.append(text)
            blocks.append(
                PageBlock(
                    text=text,
                    bbox=BBox(x0=float(x0), y0=float(y0), x1=float(x1), y1=float(y1)),
                    char_start=start,
                    char_end=start + len(text),
                    anchor=f"p.{page_index} ¶{block_index}",
                    page_no=page_index,
                )
            )
            page_text_parts.append("\n")
        page_text = "".join(page_text_parts)
        page_start = len("".join(full_parts))
        full_parts.append(page_text)
        if page_text.strip():
            full_parts.append("\n")
        pages.append(
            ParsedPage(
                page_no=page_index,
                text=page_text,
                char_start=page_start,
                char_end=page_start + len(page_text),
                blocks=blocks,
            )
        )
        cursor = len("".join(full_parts))

    full_text = "".join(full_parts)
    warnings: list[str] = []
    if len(full_text.strip()) < 20:
        raise OCRRequiredError(
            "该 PDF 无可提取文本，可能是扫描件。M0 不自动 OCR；请安装 `uv sync --extra ocr` 后使用图片解析，或提供文本版 PDF。"
        )

    tables: list[str] = []
    try:
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables() or []:
                    rows = [" | ".join((cell or "").strip() for cell in row) for row in table]
                    tables.append("\n".join(rows))
    except Exception as exc:  # 表格解析失败不能拖垮正文
        warnings.append(f"pdfplumber 表格解析失败: {type(exc).__name__}: {exc}")

    return ParsedDocument(
        doc_id=_doc_id(path, raw),
        source_path=str(path),
        source_type="pdf",
        full_text=full_text,
        pages=pages,
        tables=tables,
        warnings=warnings,
    )


def _parse_docx(path: Path, raw: bytes) -> ParsedDocument:
    from docx import Document

    doc = Document(path)
    parts: list[str] = []
    blocks: list[PageBlock] = []
    cursor = 0
    current_chapter = ""
    paragraph_no = 0

    def add_block(text: str, *, anchor: str) -> None:
        nonlocal cursor
        if not text.strip():
            return
        blocks.append(
            PageBlock(
                text=text,
                bbox=None,
                char_start=cursor,
                char_end=cursor + len(text),
                anchor=anchor,
            )
        )
        parts.append(text)
        parts.append("\n")
        cursor = len("".join(parts))

    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        if _CHAPTER_RE.match(text):
            current_chapter = text
        paragraph_no += 1
        anchor = f"§{paragraph_no}"
        if current_chapter:
            anchor = f"{anchor} {current_chapter}"
        add_block(text, anchor=anchor)
    tables: list[str] = []
    for table_index, table in enumerate(doc.tables, start=1):
        rows: list[str] = []
        for row_index, row in enumerate(table.rows, start=1):
            row_text = " | ".join(cell.text.strip() for cell in row.cells)
            rows.append(row_text)
            anchor = f"表{table_index} 行{row_index}"
            if current_chapter:
                anchor = f"{anchor} {current_chapter}"
            add_block(row_text, anchor=anchor)
        tables.append("\n".join(rows))

    full_text = "".join(parts)
    warnings = [
        "docx 无真实分页信息，page_no 置空，不按字符位置推断页码；"
        "定位锚使用段落序号 + 章节标题路径（§段落号 [章节标题]）。"
    ]
    return ParsedDocument(
        doc_id=_doc_id(path, raw),
        source_path=str(path),
        source_type="docx",
        full_text=full_text,
        pages=[
            ParsedPage(
                page_no=1,
                text=full_text,
                char_start=0,
                char_end=len(full_text),
                blocks=blocks,
            )
        ],
        tables=tables,
        warnings=warnings,
    )


def _parse_image(path: Path, raw: bytes) -> ParsedDocument:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise OCRDependencyError(
            "扫描件/图片需要 RapidOCR 可选依赖，请执行 `uv sync --extra ocr`。M0 不会静默跳过 OCR。"
        ) from exc

    engine = RapidOCR()
    result, _ = engine(str(path))
    if not result:
        raise OCRRequiredError("RapidOCR 未识别到文字，请检查图片清晰度。")
    lines = [item[1] for item in result]
    full_text = "\n".join(lines)
    blocks: list[PageBlock] = []
    cursor = 0
    for line_no, line in enumerate(lines, start=1):
        blocks.append(
            PageBlock(
                text=line,
                bbox=None,
                char_start=cursor,
                char_end=cursor + len(line),
                anchor=f"OCR 行{line_no}",
                page_no=1,
            )
        )
        cursor += len(line) + 1
    return ParsedDocument(
        doc_id=_doc_id(path, raw),
        source_path=str(path),
        source_type="image",
        full_text=full_text,
        pages=[
            ParsedPage(
                page_no=1,
                text=full_text,
                char_start=0,
                char_end=len(full_text),
                blocks=blocks,
            )
        ],
        warnings=["图片 OCR 结果可能含识别误差；M0 仅作降级路径。"],
    )
