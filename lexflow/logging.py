"""JSON 行日志。统一字段：matter_id/node/model/tokens/latency/status/device。

字段缺失时补 null，保证每次 node.done 都能按 matter_id 串起来并做耗时聚合。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

REQUIRED_FIELDS = ("matter_id", "node", "model", "tokens", "latency", "status", "device")


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
        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(os_level())
        logger.propagate = False
    return logger


def os_level() -> int:
    import os

    return getattr(logging, os.getenv("LEXFLOW_LOG_LEVEL", "INFO").upper(), logging.INFO)


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    normalized = {key: fields.pop(key, None) for key in REQUIRED_FIELDS}
    normalized.update(fields)
    logger.info(event, extra={"fields": normalized})
