"""FastAPI：/analyze、/review/{id}、/export/{id}。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from lexflow.service import AnalyzeService

app = FastAPI(title="lexflow M0", version="0.1.0")

# 延迟创建：导入模块时不加载语料/向量（测试可先注入临时目录的 service）。
service: AnalyzeService | None = None


def get_service() -> AnalyzeService:
    global service
    if service is None:
        service = AnalyzeService()
    return service


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


@app.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    stance: str = Form("甲方"),
    as_of: str | None = Form(None),
    engine: str = Form("auto"),
    rerank: bool | None = Form(None),
) -> dict[str, object]:
    suffix = Path(file.filename or "").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        handle.write(await file.read())
        temp_path = handle.name
    try:
        result = get_service().analyze(
            temp_path,
            stance=stance,
            as_of=as_of,
            engine=engine,
            rerank=rerank,
            require_confirmation=True,
            auto_approve=False,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    return {
        "matter_id": result.matter_id,
        "status": result.status,
        "engine": result.engine,
        "findings_count": result.findings_count,
        "elapsed_ms": result.elapsed_ms,
        "warnings": result.warnings,
        "metrics": result.report.get("metrics", {}),
        "report": result.report,
    }


@app.post("/review/{matter_id}")
def review(matter_id: str, request: ReviewRequest) -> dict[str, object]:
    try:
        result = get_service().review(matter_id, action=request.action, comment=request.comment)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    return {
        "matter_id": result.matter_id,
        "status": result.status,
        "engine": result.engine,
        "findings_count": result.findings_count,
        "elapsed_ms": result.elapsed_ms,
        "docx_path": result.docx_path,
        "metrics": result.report.get("metrics", {}),
    }


@app.get("/export/{matter_id}")
def export(matter_id: str) -> FileResponse:
    try:
        path = get_service().get_docx_path(matter_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
