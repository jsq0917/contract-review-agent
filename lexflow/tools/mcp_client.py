"""MCP 客户端：subprocess + JSON-RPC 2.0 over stdio。

为什么不用官方 SDK：这里只需要四个方法；手写不到 150 行，零新增依赖，
而且超时、损坏响应、子进程退出都能明确控制成可测试的失败类别。
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any

from lexflow.tools.definitions import PROTOCOL_VERSION, ToolError


class MCPClient:
    def __init__(
        self,
        args: list[str],
        *,
        timeout_s: float = 20.0,
        cwd: str | Path | None = None,
        command: list[str] | None = None,
    ) -> None:
        self.args = args
        self.timeout_s = timeout_s
        self.cwd = str(cwd) if cwd else None
        # 仅供测试注入；生产固定走 lexflow.tools.mcp_server。
        self.command = command
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=50)
        self._next_id = 0
        self._broken = False
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None

    # ---------- 生命周期 ----------

    def start(self) -> "MCPClient":
        self._process = subprocess.Popen(
            self.command or [sys.executable, "-m", "lexflow.tools.mcp_server", *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            cwd=self.cwd,
        )
        self._reader = threading.Thread(target=self._read_stdout, name="mcp-stdout", daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(
            target=self._read_stderr, name="mcp-stderr", daemon=True
        )
        self._stderr_reader.start()
        self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "lexflow", "version": "0.1.0"},
            },
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    def close(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
        self._process = None

    def __enter__(self) -> "MCPClient":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------- 协议 ----------

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._broken:
            raise ToolError("mcp_unavailable", "MCP 连接已损坏")
        self._next_id += 1
        request_id = self._next_id
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)
        try:
            response = self._responses.get(timeout=self.timeout_s)
        except queue.Empty as exc:
            self._broken = True
            raise ToolError("tool_timeout", f"MCP 请求超时（{self.timeout_s}s）: {method}") from exc
        if response.get("id") != request_id:
            self._broken = True
            raise ToolError("protocol_error", "MCP 响应 id 不匹配")
        if "error" in response:
            error = response["error"] or {}
            text = str(error.get("message", ""))
            category = "unknown_tool" if "unknown_tool" in text else "invalid_arguments"
            raise ToolError(category, text)
        return response.get("result") or {}

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content") or []
        if not content:
            raise ToolError("protocol_error", "MCP 工具返回空 content")
        text = content[0].get("text", "")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ToolError("protocol_error", f"MCP 工具返回非 JSON: {text[:120]}") from exc
        if result.get("isError"):
            raise ToolError("tool_error", text[:200])
        return payload

    # ---------- 内部 ----------

    def _send(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise ToolError("mcp_unavailable", "MCP 子进程未启动")
        try:
            process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._broken = True
            reason = self.stderr_tail()
            raise ToolError("mcp_unavailable", f"MCP 写入失败: {exc}; stderr={reason}") from exc

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._responses.put(json.loads(line))
            except json.JSONDecodeError:
                self._responses.put(
                    {"id": None, "error": {"code": -32700, "message": "客户端收到非 JSON 行"}}
                )
        self._broken = True

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            self._stderr.append(line.rstrip())

    def stderr_tail(self) -> str:
        return " | ".join(list(self._stderr)[-3:])
