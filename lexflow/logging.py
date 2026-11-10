"""JSON 行日志。统一字段：matter_id/node/model/tokens/latency/status/device。

字段缺失时补 null，保证每次 node.done 都能按 matter_id 串起来并做耗时聚合。
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

REQUIRED_FIELDS = ("matter_id", "node", "model", "tokens", "latency", "status", "device")

# 请求级 trace：同一线程内的所有日志自动带上 trace_id；不依赖每个调用点手写。
_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("lexflow_trace_id", default=None)


def set_trace_id(trace_id: str):
    return _trace_id.set(trace_id)


def reset_trace_id(token) -> None:
    _trace_id.reset(token)


def current_trace_id() -> str | None:
    return _trace_id.get()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if "trace_id" not in payload and (_trace_id.get()):
            payload["trace_id"] = _trace_id.get()
        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        # MCP server 的 stdout 是协议通道，日志必须让路到 stderr。
        stream = sys.stderr if os.getenv("LEXFLOW_LOG_STDERR") == "1" else sys.stdout
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(os_level())
        logger.propagate = False
    return logger


def os_level() -> int:
    return getattr(logging, os.getenv("LEXFLOW_LOG_LEVEL", "INFO").upper(), logging.INFO)


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    normalized = {key: fields.pop(key, None) for key in REQUIRED_FIELDS}
    normalized.update(fields)
    logger.info(event, extra={"fields": normalized})
