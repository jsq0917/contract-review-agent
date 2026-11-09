"""失败归档：每次结构/引用/Provider 失败都落一行 JSONL，不静默丢弃。

字段固定为 matter_id / node / reason / raw_output_hash / retriable / attempt，
另附 category 与 created_at 便于统计；不落原始模型输出，只落哈希。
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_payload(payload: Any) -> str:
    return hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def classify_exception(exc: Exception) -> str:
    # SchemaViolation / CitationNotFound 自带 category；避免在这里 import review 包造成循环依赖。
    category = getattr(exc, "category", None)
    if isinstance(category, str) and category:
        return category
    message = str(exc)
    if "未返回合法 JSON" in message:
        return "invalid_json"
    return f"provider_error:{type(exc).__name__}"


@dataclass(frozen=True)
class FailureRecord:
    matter_id: str
    node: str
    reason: str
    raw_output_hash: str
    retriable: bool
    attempt: int
    category: str
    created_at: str


class FailureLog:
    """线程安全的 JSONL 追加器；可在 API 多线程下直接复用。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(
        self,
        *,
        matter_id: str,
        node: str,
        reason: str,
        raw_output_hash: str,
        retriable: bool,
        attempt: int = 1,
        category: str = "unknown",
    ) -> FailureRecord:
        item = FailureRecord(
            matter_id=matter_id,
            node=node,
            reason=reason,
            raw_output_hash=raw_output_hash,
            retriable=retriable,
            attempt=attempt,
            category=category,
            created_at=_now(),
        )
        line = json.dumps(asdict(item), ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
        return item


def read_failures(path: str | Path) -> list[FailureRecord]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    records: list[FailureRecord] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        records.append(FailureRecord(**payload))
    return records


def summarize(path: str | Path) -> dict[str, Any]:
    records = read_failures(path)
    by_category: dict[str, int] = {}
    by_node: dict[str, int] = {}
    by_retriable = {"true": 0, "false": 0}
    for item in records:
        by_category[item.category] = by_category.get(item.category, 0) + 1
        by_node[item.node] = by_node.get(item.node, 0) + 1
        by_retriable["true" if item.retriable else "false"] += 1
    return {
        "path": str(path),
        "total": len(records),
        "by_category": dict(sorted(by_category.items())),
        "by_node": dict(sorted(by_node.items())),
        "by_retriable": by_retriable,
    }
