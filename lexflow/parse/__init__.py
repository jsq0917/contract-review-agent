"""文档解析：PDF / docx / 图片 OCR。"""

from lexflow.parse.parser import parse_document
from lexflow.parse.errors import OCRDependencyError, OCRRequiredError, UnsupportedFormatError

__all__ = ["parse_document", "OCRDependencyError", "OCRRequiredError", "UnsupportedFormatError"]
