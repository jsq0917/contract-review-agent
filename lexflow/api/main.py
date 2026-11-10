"""FastAPI：任务化 /analyze、/tasks、/review/{id}、/export/{id}。

外部边界（LF-O3）：

- 上传只接受白名单扩展名，按 1 MiB 分块落盘并在超限时立即删除；
- 临时文件与产物路径全部由服务端生成，客户端文件名只保留安全的叶子名；
- 所有错误统一为 ``{"error": {"code", "message"}}``，不返回堆栈、密钥或绝对路径；
- 默认要求 matter token：/review、/export、/tasks 均校验归属。
"""

from __future__ import annotations

import hmac
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, Header, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from lexflow.api.tasks import TaskManager, public_error
from lexflow.logging import get_logger, log_event
from lexflow.service import AnalyzeService

app = FastAPI(title="lexflow M0", version="0.2.0")

logger = get_logger("lexflow.api")

# 延迟创建：导入模块时不加载语料/向量（测试可先注入临时目录的 service）。
service: AnalyzeService | None = None
tasks: TaskManager | None = None
_init_lock = threading.Lock()

_UNSAFE_NAME = re.compile(r"[^0-9A-Za-z._\-\u4e00-\u9fff]")
_CHUNK = 1024 * 1024


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def get_service() -> AnalyzeService:
    global service
    if service is None:
        with _init_lock:
            if service is None:
                service = AnalyzeService()
    return service


def get_tasks() -> TaskManager:
    global tasks
    if tasks is None:
        with _init_lock:
            if tasks is None:
                svc = get_service()
                tasks = TaskManager(
                    svc,
                    max_workers=svc.settings.task_workers,
                    max_retries=svc.settings.task_max_retries,
                )
    return tasks


@app.exception_handler(ApiError)
async def _api_error_handler(_request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status_code
    )


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(_request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"loc": list(item.get("loc", [])), "msg": str(item.get("msg", ""))[:200]}
        for item in exc.errors()[:10]
    ]
    return JSONResponse(
        {"error": {"code": "invalid_request", "message": "请求参数不合法", "details": details}},
        status_code=422,
    )


@app.exception_handler(Exception)
async def _unhandled_error_handler(_request, exc: Exception) -> JSONResponse:
    error = public_error(exc)
    log_event(
        logger,
        "api.unhandled",
        node="api",
        model="",
        tokens=0,
        latency=0,
        status="error",
        error_category=error["code"],
        error_type=error["type"],
    )
    return JSONResponse(
        {"error": {"code": "internal_error", "message": "服务内部错误，请查看服务端日志"}},
        status_code=500,
    )


class ReviewRequest(BaseModel):
    action: Literal["approve", "modify", "reject"] = "approve"
    comment: str = ""


def _rerank_status(svc: AnalyzeService) -> dict[str, object]:
    """把「配置开了」「模型在」「已加载」「跑在哪个设备」分开报。

    只看一个 ``reranker_enabled`` 会把三种情况混成一种：配置开了但模型缺失、
    模型在但还没懒加载、真的可用。getattr 兜底是因为测试会替换成只有
    health_snapshot 的 stub 检索器。
    """
    reranker = getattr(svc.retriever, "reranker", None)
    warmup = getattr(svc, "warmup_state", None) or {}
    return {
        "reranker_configured": svc.settings.reranker_enabled,
        "reranker_model_found": bool(getattr(reranker, "model_present", False)),
        "reranker_available": bool(getattr(reranker, "available", False)),
        "reranker_device": getattr(reranker, "resolved_device", "") or None,
        "reranker_reason": getattr(reranker, "reason", "") or None,
        # 启动预热：running / ready / failed / skipped（关掉预热或没有模型）
        "reranker_warmup": warmup.get("status"),
        "reranker_warmup_s": warmup.get("seconds"),
    }


@app.get("/health")
def health() -> dict[str, object]:
    svc = get_service()
    snapshot = svc.retriever.health_snapshot()
    return {
        "status": "ok",
        "engine_default": "api" if svc.settings.api_key else "mock",
        "corpus_mode": svc.corpus_mode,
        "articles": len(svc.articles),
        "retrieval_mode": snapshot["retrieval_mode"],
        "vector_ready": snapshot["vector_ready"],
        "vector_backend": snapshot["vector_backend"],
        "vector_reason": snapshot["vector_reason"],
        "embedding_device": snapshot["embedding_device"],
        "vector_degraded": snapshot.get("degraded", False),
        "vector_failures": snapshot.get("vector_failures", 0),
        "next_retry_in_s": snapshot.get("next_retry_in_s"),
        **_rerank_status(svc),
    }


# ---------- 上传边界 ----------


def _safe_filename(raw: str | None, suffix: str) -> str:
    """客户端文件名只取叶子名并过滤危险字符；路径永远由服务端生成。"""
    leaf = Path(str(raw or "")).name
    stem = Path(leaf).stem
    cleaned = _UNSAFE_NAME.sub("_", stem).strip("._")
    return f"{cleaned or 'upload'}{suffix}"


async def _save_upload(file: UploadFile, svc: AnalyzeService) -> tuple[Path, str]:
    settings = svc.settings
    raw_name = str(file.filename or "")
    suffix = Path(raw_name).suffix.lower()
    if not suffix or suffix not in settings.allowed_extensions:
        raise ApiError(
            400,
            "unsupported_file_type",
            f"不支持的文件类型：{suffix or '无扩展名'}；允许 {', '.join(settings.allowed_extensions)}",
        )
    filename = _safe_filename(raw_name, suffix)
    upload_dir = settings.data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="lexflow-upload-", suffix=suffix, dir=upload_dir)
    temp_path = Path(temp_name)
    total = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            while True:
                chunk = await file.read(_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise ApiError(
                        413,
                        "file_too_large",
                        f"文件超过上限 {settings.max_upload_bytes} 字节",
                    )
                handle.write(chunk)
        if total == 0:
            raise ApiError(400, "empty_file", "文件为空")
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path, filename


def _check_matter_token(svc: AnalyzeService, matter_id: str, token: str | None) -> dict:
    matter = svc.store.get_matter(matter_id)
    if not matter:
        raise ApiError(404, "matter_not_found", f"matter 不存在: {matter_id}")
    if svc.settings.api_require_token:
        expected = str(matter.get("token") or "")
        if not expected or not token or not hmac.compare_digest(str(token), expected):
            raise ApiError(403, "forbidden", "matter token 缺失或不匹配")
    return matter


# ---------- 任务化审查 ----------


@app.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    stance: str = Form("甲方"),
    as_of: str | None = Form(None),
    engine: str = Form("auto"),
    rerank: bool | None = Form(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> JSONResponse:
    svc = get_service()
    temp_path, filename = await _save_upload(file, svc)
    try:
        record = get_tasks().submit(
            temp_path,
            filename=filename,
            stance=stance,
            as_of=as_of,
            engine=engine,
            rerank=rerank,
            require_confirmation=True,
            auto_approve=False,
            idempotency_key=idempotency_key,
        )
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    if record.get("idempotent_replay"):
        # 幂等重放不会入队，重复上传的临时文件立即清理。
        temp_path.unlink(missing_ok=True)
    # 幂等重放返回 200，新任务返回 202；两者 body 结构一致。
    return JSONResponse(record, status_code=200 if record.get("idempotent_replay") else 202)


@app.get("/tasks/{task_id}")
def task_status(
    task_id: str, token: str | None = Header(None, alias="X-Matter-Token")
) -> dict[str, object]:
    manager = get_tasks()
    record = manager.record(task_id)
    if not record:
        raise ApiError(404, "task_not_found", f"任务不存在: {task_id}")
    _check_matter_token(get_service(), record["matter_id"], token)
    described = manager.describe(record)
    return described


@app.post("/tasks/{task_id}/cancel")
def task_cancel(
    task_id: str, token: str | None = Header(None, alias="X-Matter-Token")
) -> dict[str, object]:
    manager = get_tasks()
    record = manager.record(task_id)
    if not record:
        raise ApiError(404, "task_not_found", f"任务不存在: {task_id}")
    _check_matter_token(get_service(), record["matter_id"], token)
    try:
        return manager.cancel(task_id)
    except LookupError as exc:
        raise ApiError(404, "task_not_found", str(exc)) from exc
    except ValueError as exc:
        raise ApiError(409, "task_not_cancellable", str(exc)) from exc


@app.post("/tasks/{task_id}/retry")
def task_retry(
    task_id: str, token: str | None = Header(None, alias="X-Matter-Token")
) -> JSONResponse:
    manager = get_tasks()
    record = manager.record(task_id)
    if not record:
        raise ApiError(404, "task_not_found", f"任务不存在: {task_id}")
    _check_matter_token(get_service(), record["matter_id"], token)
    try:
        payload = manager.retry(task_id)
    except LookupError as exc:
        raise ApiError(404, "task_not_found", str(exc)) from exc
    except FileNotFoundError as exc:
        raise ApiError(409, "source_missing", str(exc)) from exc
    except ValueError as exc:
        raise ApiError(409, "retry_not_allowed", str(exc)) from exc
    return JSONResponse(payload, status_code=202)


# ---------- 人工复核与导出 ----------


@app.post("/review/{matter_id}")
def review(
    matter_id: str,
    request: ReviewRequest,
    token: str | None = Header(None, alias="X-Matter-Token"),
) -> dict[str, object]:
    svc = get_service()
    matter = _check_matter_token(svc, matter_id, token)
    if matter.get("status") == "cancelled":
        raise ApiError(409, "matter_cancelled", "matter 已取消，不能复核或导出")
    try:
        result = svc.review(matter_id, action=request.action, comment=request.comment)
    except KeyError as exc:
        raise ApiError(404, "matter_not_found", str(exc)) from exc
    except RuntimeError as exc:
        raise ApiError(409, "matter_conflict", str(exc)) from exc
    return {
        "matter_id": result.matter_id,
        "status": result.status,
        "engine": result.engine,
        "trace_id": result.trace_id,
        "findings_count": result.findings_count,
        "elapsed_ms": result.elapsed_ms,
        "docx_path": result.docx_path,
        "metrics": result.report.get("metrics", {}),
    }


@app.get("/export/{matter_id}")
def export(
    matter_id: str,
    token: str | None = None,
    header_token: str | None = Header(None, alias="X-Matter-Token"),
) -> FileResponse:
    svc = get_service()
    # 浏览器下载链接无法带自定义 header，允许 ?token= 查询参数；只用于只读导出。
    matter = _check_matter_token(svc, matter_id, header_token or token)
    if matter.get("status") == "cancelled":
        raise ApiError(409, "matter_cancelled", "matter 已取消，不能导出")
    try:
        path = svc.get_docx_path(matter_id)
    except KeyError as exc:
        raise ApiError(404, "matter_not_found", str(exc)) from exc
    except FileNotFoundError as exc:
        raise ApiError(409, "docx_not_ready", str(exc)) from exc
    return FileResponse(
        path,
        filename=f"{matter_id}-risk-report.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


def run() -> None:
    import uvicorn

    uvicorn.run("lexflow.api.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
