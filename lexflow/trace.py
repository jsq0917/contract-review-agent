"""请求级 Trace：一个 trace_id 串起上传、检索、两路审查、工具调用、五闸、HITL 与导出。

落盘为 ``artifacts/<matter_id>/trace.jsonl``，一行一个事件。写入前统一脱敏：

- 键名包含 key/secret/authorization/token 的字段直接替换为 ``***``；
- 形如 ``sk-`` 开头的字符串做正则替换；
- 超长正文截断并记录原长度，不落完整合同文本。

trace 里出现的模型、Prompt 版本、语料版本都能回放到离线评测。
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

TRACE_VERSION = "1"
MAX_FIELD_CHARS = 300
SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
SENSITIVE_KEYS = ("api_key", "apikey", "secret", "authorization", "password", "access_token")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize(value: Any, *, max_chars: int = MAX_FIELD_CHARS) -> Any:
    """递归脱敏 + 截断，保证 trace 不含密钥和完整正文。"""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in SENSITIVE_KEYS):
                result[str(key)] = "***"
            else:
                result[str(key)] = sanitize(item, max_chars=max_chars)
        return result
    if isinstance(value, (list, tuple, set)):
        return [sanitize(item, max_chars=max_chars) for item in value]
    if isinstance(value, str):
        text = SECRET_RE.sub("sk-***", value)
        if len(text) > max_chars:
            return f"{text[:max_chars]}…<truncated:{len(text)}>"
        return text
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return sanitize(str(value), max_chars=max_chars)


class Tracer:
    """线程安全的 JSONL 追加器；一个 matter 一个实例。"""

    def __init__(
        self,
        trace_id: str,
        path: str | Path,
        *,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.trace_id = trace_id
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._meta = sanitize(meta or {})
        self.record(
            "trace.start",
            trace_version=TRACE_VERSION,
            **self._meta,
        )

    def record(self, event: str, **fields: Any) -> None:
        payload = {
            "ts": _now(),
            "trace_id": self.trace_id,
            "event": event,
            **sanitize(fields),
        }
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")

    def child_meta(self) -> dict[str, Any]:
        return {"trace_id": self.trace_id, **self._meta}


def read_trace(path: str | Path) -> list[dict[str, Any]]:
    """读取 trace；正在写入的半行（长任务轮询时常见）直接跳过，不抛异常。"""
    file_path = Path(path)
    if not file_path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def event_names(events: Iterable[dict[str, Any]]) -> list[str]:
    return [str(item.get("event", "")) for item in events]
