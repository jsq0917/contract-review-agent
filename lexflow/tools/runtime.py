"""工具执行层：本地直调或真实 MCP 子进程；超时与降级都在这里收口。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

from lexflow.config import Settings
from lexflow.models import Clause
from lexflow.retrieval.corpus import LawArticle
from lexflow.retrieval.hybrid import HybridRetriever
from lexflow.tools.definitions import ToolError
from lexflow.tools.mcp_client import MCPClient
from lexflow.tools.service import CitationToolService


class ToolRuntime:
    backend = "local"

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        return None


class LocalToolRuntime(ToolRuntime):
    """进程内直调；用于 MCP 不可用或显式关闭 MCP 的降级路径。"""

    backend = "local"

    def __init__(self, tools: CitationToolService, *, timeout_s: float = 20.0) -> None:
        self.tools = tools
        self.timeout_s = timeout_s
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lexflow-tool")

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        future = self._pool.submit(self.tools.call, name, arguments)
        try:
            return future.result(timeout=self.timeout_s)
        except FutureTimeout as exc:
            raise ToolError("tool_timeout", f"工具调用超时（{self.timeout_s}s）: {name}") from exc

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


class MCPToolRuntime(ToolRuntime):
    """真实 MCP 客户端：一个 matter 一个子进程会话。"""

    backend = "mcp"

    def __init__(self, client: MCPClient) -> None:
        self.client = client

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.client.call_tool(name, arguments)

    def close(self) -> None:
        self.client.close()


def _write_clauses(matter_id: str, settings: Settings, clauses: list[Clause]) -> Path:
    path = settings.artifacts_dir / matter_id / "clauses.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([item.model_dump(mode="json") for item in clauses], ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def build_tool_runtime(
    settings: Settings,
    *,
    matter_id: str,
    articles: list[LawArticle],
    retriever: HybridRetriever,
    clauses: list[Clause],
) -> tuple[ToolRuntime, str | None]:
    """优先 MCP；启动失败返回本地运行时和一条可记录的降级原因。"""

    local = LocalToolRuntime(
        CitationToolService(articles, retriever, clauses), timeout_s=settings.mcp_timeout_s
    )
    if not settings.mcp_enabled:
        return local, "LEXFLOW_MCP_ENABLED=false，使用进程内工具"
    try:
        clauses_path = _write_clauses(matter_id, settings, clauses)
        client = MCPClient(
            ["--corpus", str(settings.corpus_path), "--clauses", str(clauses_path)],
            timeout_s=settings.mcp_timeout_s,
        ).start()
        return MCPToolRuntime(client), None
    except Exception as exc:  # noqa: BLE001 - 启动失败必须可降级
        return local, f"MCP 启动失败，降级为进程内工具: {type(exc).__name__}: {exc}"
