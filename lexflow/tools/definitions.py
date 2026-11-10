"""引用补证工具的唯一声明源：MCP ``tools/list`` 与 Function Calling 共用。

只暴露三个**只读**工具。没有任何写文件、导出、执行命令的入口——模型即使被
合同正文注入，也只能查到语料里的法条，不能触发导出或写盘。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

TOOL_NAMES = ("search_statutes", "get_article", "get_clause_context")
MAX_TOP_K = 5
PROTOCOL_VERSION = "2024-11-05"

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "search_statutes",
        "description": "按条款文本/争点检索候选法条，只读本地语料。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2, "maxLength": 500},
                "top_k": {"type": "integer", "minimum": 1, "maximum": MAX_TOP_K, "default": 3},
                "law_name": {
                    "type": "string",
                    "maxLength": 40,
                    "description": "可选，限定法律名称，如 中华人民共和国劳动合同法",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_article",
        "description": "按法律名称 + 条号读取语料中的法条原文；不存在时返回 found=false。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "law_name": {"type": "string", "minLength": 2, "maxLength": 40},
                "article_no": {"type": "string", "minLength": 1, "maxLength": 16},
            },
            "required": ["law_name", "article_no"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_clause_context",
        "description": "按 clause_id 读取当前合同条款原文，用于核对引文出处。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "clause_id": {"type": "string", "pattern": "^C[0-9]{4,6}$"},
            },
            "required": ["clause_id"],
            "additionalProperties": False,
        },
    },
]


def openai_tools() -> list[dict[str, Any]]:
    """转成 OpenAI tools 参数格式。"""
    return [
        {
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec["description"],
                "parameters": spec["inputSchema"],
            },
        }
        for spec in TOOL_SPECS
    ]


class SearchStatutesArgs(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    top_k: int = Field(default=3, ge=1, le=MAX_TOP_K)
    law_name: str | None = Field(default=None, max_length=40)


class GetArticleArgs(BaseModel):
    law_name: str = Field(min_length=2, max_length=40)
    article_no: str = Field(min_length=1, max_length=16)


class GetClauseContextArgs(BaseModel):
    clause_id: str = Field(pattern=r"^C[0-9]{4,6}$")


ARG_MODELS: dict[str, type[BaseModel]] = {
    "search_statutes": SearchStatutesArgs,
    "get_article": GetArticleArgs,
    "get_clause_context": GetClauseContextArgs,
}


class ToolError(RuntimeError):
    """工具层错误：未知工具、参数非法、超时、后端不可用。"""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def validate_args(name: str, arguments: dict[str, Any]) -> BaseModel:
    if name not in TOOL_NAMES:
        raise ToolError("unknown_tool", f"未知工具: {name}")
    if not isinstance(arguments, dict):
        raise ToolError("invalid_arguments", "工具参数必须是对象")
    model = ARG_MODELS[name]
    try:
        return model(**arguments)
    except ValidationError as exc:
        raise ToolError("invalid_arguments", f"工具参数不合法: {exc.errors()[:2]}") from exc


class CitationAnswer(BaseModel):
    """补证模型的最终回答；必须显式给出 resolved 或 no_applicable_law。"""

    status: Literal["resolved", "no_applicable_law"]
    law_name: str | None = None
    article_no: str | None = None
    cited_quote: str | None = None
    reason: str = ""
    confidence: Literal["high", "medium", "low"] = "low"
