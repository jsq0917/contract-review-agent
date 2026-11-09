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
        "reranker_enabled": svc.settings.reranker_enabled,
    }


@app.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    stance: str = Form("甲方"),
    as_of: str | None = Form(None),
    engine: str = Form("auto"),
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
