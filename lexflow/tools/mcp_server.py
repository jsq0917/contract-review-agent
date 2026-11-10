"""最小 MCP Server：JSON-RPC 2.0 over stdio，只暴露三个只读工具。

不引入 MCP SDK：协议面只有 initialize / tools/list / tools/call / ping，
用标准库即可，避免为一个只读检索工具新增运行时依赖。

启动方式（由 ``mcp_client`` 自动拉起）::

    python -m lexflow.tools.mcp_server --corpus data/corpus/law_articles.jsonl \
        --clauses artifacts/<matter>/clauses.json

stdout 只允许出现协议响应；日志一律走 stderr。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# stdout 是 JSON-RPC 通道：在导入业务模块前就把日志切到 stderr。
os.environ.setdefault("LEXFLOW_LOG_STDERR", "1")

from lexflow.retrieval.corpus import load_corpus
from lexflow.retrieval.hybrid import HybridRetriever
from lexflow.tools.definitions import PROTOCOL_VERSION, TOOL_SPECS, ToolError
from lexflow.tools.service import CitationToolService

SERVER_INFO = {"name": "lexflow-statutes", "version": "0.1.0"}


class MCPExchange:
    """把一条 JSON-RPC 消息映射成一条响应；通知消息返回 None。"""

    def __init__(self, tools: CitationToolService) -> None:
        self.tools = tools

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")
        if request_id is None:
            # notifications/initialized 等通知不回包。
            return None
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": spec["name"],
                            "description": spec["description"],
                            "inputSchema": spec["inputSchema"],
                        }
                        for spec in TOOL_SPECS
                    ]
                }
            elif method == "tools/call":
                params = message.get("params") or {}
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    return _error(request_id, -32602, "arguments 必须是对象")
                try:
                    payload = self.tools.call(str(name), arguments)
                except ToolError as exc:
                    return _error(request_id, -32602, f"{exc.category}: {exc}")
                result = {
                    "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                    "isError": False,
                }
            else:
                return _error(request_id, -32601, f"未知方法: {method}")
        except ToolError as exc:
            return _error(request_id, -32602, f"{exc.category}: {exc}")
        except Exception as exc:  # noqa: BLE001 - 协议层兜底
            return _error(request_id, -32603, f"{type(exc).__name__}: {exc}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def build_tools(corpus_path: str | Path, clauses_path: str | Path | None) -> CitationToolService:
    from lexflow.models import Clause

    articles, _ = load_corpus(corpus_path)
    retriever = HybridRetriever(
        articles,
        embedding_model="",
        embedding_enabled=False,
        reranker_model="",
        reranker_enabled=False,
    )
    clauses = []
    if clauses_path:
        raw = json.loads(Path(clauses_path).read_text(encoding="utf-8"))
        clauses = [Clause(**item) for item in raw]
    return CitationToolService(articles, retriever, clauses)


def serve(tools: CitationToolService) -> int:
    exchange = MCPExchange(tools)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = _error(None, -32700, "JSON 解析失败")
        else:
            response = exchange.handle(message)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="lexflow 法条检索 MCP server")
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--clauses", default="")
    args = parser.parse_args(argv)
    for stream in (sys.stdin, sys.stdout):
        stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    tools = build_tools(args.corpus, args.clauses or None)
    return serve(tools)


if __name__ == "__main__":
    raise SystemExit(main())
