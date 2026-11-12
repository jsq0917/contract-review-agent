"""单机最小任务化：线程池 + SQLite 任务表 + trace 进度（LF-N2）。

不引入 Redis/Celery：审查在本进程内完成，``POST /analyze`` 立即返回 task_id，
后台线程执行解析、两路审查、补证、五闸与报告组装。取消是协作式的，只在
任务开始前和图返回后生效，不会强行中断正在执行的 LLM 调用；这一点如实写进
文档，避免把「可取消」说成「秒级中断」。
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from lexflow.config import Settings
from lexflow.llm.errors import classify_exception
from lexflow.logging import get_logger, log_event
from lexflow.service import AnalyzeService
from lexflow.trace import SECRET_RE, read_trace

logger = get_logger("lexflow.tasks")

TERMINAL = {"awaiting_review", "completed", "rejected", "failed", "cancelled", "interrupted"}
RETRYABLE = {"failed", "cancelled", "interrupted"}

_WIN_PATH = re.compile(r"[A-Za-z]:\\[^\s，。；：\"']+")
_POSIX_PATH = re.compile(r"(?<![\w.])/(?:[\w.\-]+/)*[\w.\-]+")


def public_error(exc: BaseException) -> dict[str, Any]:
    """给客户端的错误：只保留类别与脱敏后的短消息，不带堆栈/绝对路径/密钥。"""
    category = getattr(exc, "category", None) or classify_exception(exc).category
    message = SECRET_RE.sub("sk-***", str(exc))
    message = _WIN_PATH.sub("<path>", message)
    message = _POSIX_PATH.sub("<path>", message)
    if len(message) > 200:
        message = message[:200] + "…"
    return {"code": category, "type": type(exc).__name__, "message": message}


class TaskManager:
    def __init__(
        self,
        service: AnalyzeService,
        *,
        max_workers: int = 2,
        max_retries: int = 1,
    ) -> None:
        self.service = service
        self.store = service.store
        self.settings: Settings = service.settings
        self.max_retries = max_retries
        self._lock = threading.RLock()
        self._cancel: dict[str, threading.Event] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, max_workers), thread_name_prefix="lexflow-task"
        )
        self._recover()

    # ---------- 生命周期 ----------

    def _recover(self) -> None:
        """服务重启后把未完成任务标成 interrupted；matter 的 checkpoint 仍可复核。"""
        for task in self.store.list_unfinished_tasks():
            self.store.update_task(
                task["id"],
                status="interrupted",
                error_code="service_restart",
                error_message="服务重启导致任务中断；可 retry 重新执行，或用 matter_id 从 checkpoint 复核",
            )
            self.store.update_matter(task["matter_id"], status="interrupted")

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ---------- 提交 ----------

    def submit(
        self,
        source_path: Path,
        *,
        filename: str,
        stance: str,
        as_of: str | None,
        engine: str,
        rerank: bool | None,
        require_confirmation: bool = True,
        auto_approve: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = (idempotency_key or "").strip() or None
        if key:
            existing = self.store.get_task_by_idempotency(key)
            if existing:
                # 同一幂等键只复用已有任务，不新建、不重复扣费。
                return {**self.describe(existing), "idempotent_replay": True}

        matter_id = f"matter-{uuid.uuid4().hex[:10]}"
        task_id = f"task-{uuid.uuid4().hex[:10]}"
        token = secrets.token_urlsafe(24)
        trace_id = f"trace-{matter_id}"
        # 先在 matters 建行：任务刚入队时 /tasks 与 token 校验就能查到归属。
        self.store.save_matter(
            matter_id,
            filename,
            engine,
            stance,
            as_of or "",
            token=token,
        )
        self.store.create_task(
            task_id,
            matter_id,
            idempotency_key=key,
            trace_id=trace_id,
        )
        self._enqueue(
            task_id,
            {
                "source_path": str(source_path),
                "cleanup": True,
                "filename": filename,
                "stance": stance,
                "as_of": as_of,
                "engine": engine,
                "rerank": rerank,
                "require_confirmation": require_confirmation,
                "auto_approve": auto_approve,
                "matter_id": matter_id,
                "token": token,
            },
        )
        return {
            "task_id": task_id,
            "matter_id": matter_id,
            "status": "queued",
            "trace_id": trace_id,
            "token": token,
            "idempotent_replay": False,
        }

    def _enqueue(self, task_id: str, payload: dict[str, Any]) -> None:
        event = threading.Event()
        with self._lock:
            self._cancel[task_id] = event
        self._executor.submit(self._run, task_id, payload, event)

    # ---------- 后台执行 ----------

    def _run(self, task_id: str, payload: dict[str, Any], event: threading.Event) -> None:
        matter_id = payload["matter_id"]
        try:
            if event.is_set():
                self._finish(
                    task_id,
                    "cancelled",
                    error_code="cancelled",
                    error_message="任务在开始前被取消",
                )
                self.store.update_matter(matter_id, status="cancelled")
                return
            self.store.update_task(task_id, status="running", error_code=None, error_message=None)
            result = self.service.analyze(
                payload["source_path"],
                stance=payload["stance"],
                as_of=payload["as_of"],
                engine=payload["engine"],
                require_confirmation=payload["require_confirmation"],
                auto_approve=payload["auto_approve"],
                rerank=payload["rerank"],
                matter_id=matter_id,
                token=payload["token"],
            )
            if event.is_set():
                # 图已返回但用户已取消：不进入人工复核，matter 标记取消，后续 /review 与 /export 拒绝。
                self.store.update_matter(matter_id, status="cancelled")
                self._finish(
                    task_id,
                    "cancelled",
                    error_code="cancelled",
                    error_message="任务在审查结束前被取消，结果不进入人工复核",
                )
                return
            self._finish(task_id, result.status)
            log_event(
                logger,
                "task.done",
                matter_id=matter_id,
                node="task",
                model=result.engine,
                tokens=0,
                latency=result.elapsed_ms,
                status=result.status,
            )
        except Exception as exc:  # noqa: BLE001 - 后台任务必须兜住，否则线程静默退出
            error = public_error(exc)
            self.store.update_matter(matter_id, status="failed")
            self._finish(
                task_id,
                "failed",
                error_code=error["code"],
                error_message=error["message"],
            )
            log_event(
                logger,
                "task.failed",
                matter_id=matter_id,
                node="task",
                model="",
                tokens=0,
                latency=0,
                status="error",
                error_category=error["code"],
            )
        finally:
            with self._lock:
                self._cancel.pop(task_id, None)
            if payload.get("cleanup"):
                try:
                    Path(payload["source_path"]).unlink(missing_ok=True)
                except OSError:
                    pass

    def _finish(
        self,
        task_id: str,
        status: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.store.update_task(
            task_id,
            status=status,
            error_code=error_code,
            error_message=error_message,
        )

    # ---------- 查询 / 控制 ----------

    def get(self, task_id: str) -> dict[str, Any] | None:
        task = self.store.get_task(task_id)
        return self.describe(task) if task else None

    def record(self, task_id: str) -> dict[str, Any] | None:
        return self.store.get_task(task_id)

    def matter_of(self, task: dict[str, Any]) -> dict[str, Any] | None:
        return self.store.get_matter(task["matter_id"])

    def cancel(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if not task:
            raise LookupError(f"任务不存在: {task_id}")
        status = task["status"]
        if status in ("completed", "rejected", "failed", "cancelled", "interrupted"):
            raise ValueError(f"任务已结束（{status}），不能取消")
        if status == "awaiting_review":
            raise ValueError("审查已完成，任务处于人工复核阶段；如需终止请驳回或忽略该 matter")
        with self._lock:
            event = self._cancel.get(task_id)
        if event is None:
            # 服务重启后恢复出来的任务不在线程池里，直接落终态。
            self.store.update_matter(task["matter_id"], status="cancelled")
            self.store.update_task(
                task_id, status="cancelled", error_code="cancelled", error_message="任务已取消"
            )
        else:
            event.set()
            self.store.update_task(task_id, status="cancelling")
        return self.describe(self.store.get_task(task_id) or task)

    def retry(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if not task:
            raise LookupError(f"任务不存在: {task_id}")
        if task["status"] not in RETRYABLE:
            raise ValueError(f"只有失败/取消/中断的任务可以重试，当前为 {task['status']}")
        attempts = int(task["attempts"])
        if attempts > self.max_retries or self.store.has_retry_child(task_id):
            raise ValueError(f"重试次数已达上限（{self.max_retries} 次）")
        matter = self.store.get_matter(task["matter_id"])
        if not matter:
            raise LookupError(f"matter 不存在: {task['matter_id']}")
        source = self.settings.artifacts_dir / task["matter_id"] / str(matter["filename"])
        if not source.exists():
            raise FileNotFoundError("原始文件已被清理，无法重试；请重新上传")

        new_task_id = f"task-{uuid.uuid4().hex[:10]}"
        new_matter_id = f"matter-{uuid.uuid4().hex[:10]}"
        token = secrets.token_urlsafe(24)
        self.store.save_matter(
            new_matter_id,
            str(matter["filename"]),
            str(matter["engine"]),
            str(matter["stance"]),
            str(matter["as_of"]),
            token=token,
        )
        self.store.create_task(
            new_task_id,
            new_matter_id,
            attempts=attempts + 1,
            retry_of=task_id,
            trace_id=f"trace-{new_matter_id}",
        )
        self._enqueue(
            new_task_id,
            {
                "source_path": str(source),
                "cleanup": False,
                "filename": str(matter["filename"]),
                "stance": str(matter["stance"]),
                "as_of": str(matter["as_of"]),
                "engine": str(matter["engine"]),
                "rerank": None,
                "require_confirmation": True,
                "auto_approve": False,
                "matter_id": new_matter_id,
                "token": token,
            },
        )
        return {
            "task_id": new_task_id,
            "matter_id": new_matter_id,
            "status": "queued",
            "trace_id": f"trace-{new_matter_id}",
            "token": token,
            "retry_of": task_id,
            "attempts": attempts + 1,
        }

    def describe(self, task: dict[str, Any]) -> dict[str, Any]:
        matter = self.store.get_matter(task["matter_id"]) or {}
        status = task["status"]
        if status == "awaiting_review" and matter.get("status") in (
            "completed",
            "rejected",
            "cancelled",
        ):
            # 人工复核会改变 matter 状态，任务查询应当反映最新状态。
            status = str(matter["status"])
        return {
            "task_id": task["id"],
            "matter_id": task["matter_id"],
            "status": status,
            "attempts": int(task["attempts"]),
            "retry_of": task.get("retry_of"),
            "trace_id": task.get("trace_id") or f"trace-{task['matter_id']}",
            "error": (
                {"code": task["error_code"], "message": task["error_message"]}
                if task.get("error_code")
                else None
            ),
            "progress": self._progress(task["matter_id"]),
            "result": self._result(task["matter_id"], matter, status),
        }

    def _progress(self, matter_id: str) -> dict[str, Any]:
        events = read_trace(self.settings.artifacts_dir / matter_id / "trace.jsonl")
        nodes = [str(item.get("node")) for item in events if item.get("event") == "node.end"]
        last = events[-1] if events else {}
        return {
            "events": len(events),
            "last_event": last.get("event"),
            "nodes_done": nodes,
            "updated_at": last.get("ts"),
        }

    def _result(
        self, matter_id: str, matter: dict[str, Any], status: str
    ) -> dict[str, Any] | None:
        if status not in ("awaiting_review", "completed", "rejected"):
            return None
        raw = matter.get("result_json")
        if not raw:
            return None
        try:
            report = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return {
            "matter_id": matter_id,
            "status": status,
            "engine": matter.get("engine"),
            "findings_count": len(report.get("findings", [])),
            "docx_path": matter.get("docx_path") or "",
            "trace_id": f"trace-{matter_id}",
            "metrics": (report.get("metrics") or {}),
            "report": report,
        }
