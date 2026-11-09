"""解析相关显式错误：不静默吞掉扫描件/缺依赖。"""


class ParseError(Exception):
    pass


class UnsupportedFormatError(ParseError):
    pass


class OCRRequiredError(ParseError):
    """PDF 无可提取文本，需要 OCR，但 M0 未自动启用。"""


class OCRDependencyError(ParseError):
    """扫描件需要 RapidOCR，但可选依赖未安装。"""
