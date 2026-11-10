"""API 层测试：TestClient + 临时目录，不写真实 data/。

覆盖 /health（indexing→hybrid 状态流转）、任务化 /analyze + /tasks、/review、/export，
以及“进程中断后用新 service 实例按 matter_id 恢复”的 API 路径。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import lexflow.api.main as api_main
from lexflow.config import ROOT
from lexflow.service import AnalyzeService

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture()
def client(settings, monkeypatch):
    svc = AnalyzeService(settings)
    monkeypatch.setattr(api_main, "service", svc)
    monkeypatch.setattr(api_main, "tasks", None)
    with TestClient(api_main.app) as test_client:
        yield test_client, svc
    manager = api_main.tasks
    if manager is not None:
        manager.shutdown()


def _upload(client: TestClient, path: Path, headers: dict | None = None, **form):
    with path.open("rb") as handle:
        return client.post(
            "/analyze",
            files={"file": (path.name, handle.read(), DOCX_MIME)},
            data={"stance": "甲方", "as_of": "2026-11-03", "engine": "mock", **form},
            headers=headers or {},
        )


def _wait(client: TestClient, record: dict, timeout: float = 120.0) -> dict:
    headers = {"X-Matter-Token": record["token"]}
    deadline = time.time() + timeout
    payload: dict = record
    while time.time() < deadline:
        response = client.get(f"/tasks/{record['task_id']}", headers=headers)
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] not in ("queued", "running", "cancelling"):
            return payload
        time.sleep(0.05)
    raise AssertionError(f"任务未在 {timeout}s 内结束: {payload}")


def _analyze(client: TestClient, path: Path, **form) -> dict:
    response = _upload(client, path, **form)
    assert response.status_code == 202, response.text
    record = response.json()
    record["matter_id"]
    return _wait(client, record)


def test_health_fields_and_corpus(client):
    test_client, svc = client
    response = test_client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["engine_default"] == ("api" if svc.settings.api_key else "mock")
    assert payload["corpus_mode"] == "external"
    assert payload["articles"] == 1465
    # 首次调用时后台线程刚启动，可能短暂处于 indexing；等待其落到最终模式。
    deadline = time.time() + 2.0
    while payload["retrieval_mode"] == "indexing" and time.time() < deadline:
        time.sleep(0.02)
        payload = test_client.get("/health").json()
    # 测试配置 embedding_enabled=False：最终 BM25-only，不应谎报 hybrid。
    assert payload["retrieval_mode"] == "bm25-only"
    assert payload["vector_ready"] is False
    # 精排状态拆成四项：配置、模型是否存在、是否已加载、跑在哪。
    assert payload["reranker_configured"] is False
    assert payload["reranker_model_found"] is False
    assert payload["reranker_available"] is False
    assert payload["reranker_device"] is None
    # 没有模型时不预热，状态要如实说 skipped 而不是卡在 running。
    assert payload["reranker_warmup"] == "skipped"
    assert payload["reranker_warmup_s"] is None


def test_analyze_with_rerank_requested_but_unavailable(client):
    """请求精排但模型未配置：任务正常结束、降级 hybrid，并在 metrics 里说清没生效。"""
    test_client, _svc = client
    payload = _analyze(
        test_client, ROOT / "examples" / "sample_service_contract.docx", rerank="true"
    )
    assert payload["status"] == "awaiting_review"
    metrics = payload["result"]["metrics"]
    assert metrics["rerank_requested"] is True
    assert metrics["rerank_used"] is False
    assert metrics["rerank_applied"] == 0
    assert metrics["rerank_fallback"] > 0
    assert "未配置" in metrics["rerank_reason"]


def test_analyze_without_rerank_keeps_hybrid(client):
    """不传 rerank 时取 settings 默认（False），不发起任何精排调用。"""
    test_client, _svc = client
    payload = _analyze(test_client, ROOT / "examples" / "sample_service_contract.docx")
    metrics = payload["result"]["metrics"]
    assert metrics["rerank_requested"] is False
    assert metrics["rerank_used"] is False
    assert metrics["rerank_applied"] == 0
    assert metrics["rerank_reason"] is None


def test_health_indexing_then_hybrid(client):
    test_client, svc = client

    class _StubRetriever:
        """用可控快照验证 /health 的 indexing → hybrid 状态透传。"""

        def __init__(self) -> None:
            self.calls = 0

        def health_snapshot(self) -> dict[str, object]:
            self.calls += 1
            if self.calls == 1:
                return {
                    "retrieval_mode": "indexing",
                    "vector_ready": False,
                    "vector_backend": None,
                    "vector_reason": None,
                    "embedding_device": "cuda",
                }
            return {
                "retrieval_mode": "hybrid",
                "vector_ready": True,
                "vector_backend": "cache",
                "vector_reason": None,
                "embedding_device": "cuda",
            }

    svc.retriever = _StubRetriever()

    first = test_client.get("/health").json()
    assert first["retrieval_mode"] == "indexing"
    assert first["vector_ready"] is False
    assert first["embedding_device"] == "cuda"

    second = test_client.get("/health").json()
    assert second["retrieval_mode"] == "hybrid"
    assert second["vector_ready"] is True
    assert second["vector_backend"] == "cache"
    assert second["embedding_device"] == "cuda"


def test_analyze_review_export(client):
    test_client, _svc = client
    response = _upload(test_client, ROOT / "examples" / "sample_service_contract.docx")
    assert response.status_code == 202, response.text
    record = response.json()
    assert record["status"] == "queued"
    assert record["matter_id"].startswith("matter-")
    assert record["task_id"].startswith("task-")
    assert record["token"]

    task = _wait(test_client, record)
    assert task["status"] == "awaiting_review"
    payload = task["result"]
    assert payload["status"] == "awaiting_review"
    assert payload["engine"] == "mock"
    assert payload["findings_count"] > 0
    assert payload["report"]["doc_id"]
    metrics = payload["metrics"]
    assert metrics["llm_calls"] > 0
    assert metrics["prompt_tokens"] == 0 and metrics["completion_tokens"] == 0
    for finding in payload["report"]["findings"]:
        assert finding["rule_id"]
        assert finding["law_name"] and finding["article_no"]
        assert finding["locator"]
        assert finding["doc_id"] == payload["report"]["doc_id"]

    matter_id = payload["matter_id"]
    headers = {"X-Matter-Token": record["token"]}
    assert test_client.get(f"/export/{matter_id}", headers=headers).status_code == 409

    reviewed = test_client.post(
        f"/review/{matter_id}",
        json={"action": "approve", "comment": "API 确认"},
        headers=headers,
    )
    assert reviewed.status_code == 200
    body = reviewed.json()
    assert body["status"] == "completed"
    assert body["findings_count"] == payload["findings_count"]
    assert Path(body["docx_path"]).exists()
    assert "export_docx" in body["metrics"]["node_latency_ms"]
    # review 是续跑 export_docx，没有检索发生：不能把 analyze 的精排指标覆盖成零值。
    assert body["metrics"]["rerank_requested"] is False
    assert body["metrics"]["rerank_used"] is False

    exported = test_client.get(f"/export/{matter_id}", headers=headers)
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith(DOCX_MIME)
    assert matter_id in exported.headers["content-disposition"]
    assert len(exported.content) > 0

    # 重复 review 不再跑图：直接回放已完成结果。
    again = test_client.post(f"/review/{matter_id}", json={"action": "approve"}, headers=headers)
    assert again.status_code == 200
    assert again.json()["elapsed_ms"] == 0
    assert again.json()["findings_count"] == payload["findings_count"]


def test_review_resume_across_new_service_instance(client, settings, monkeypatch):
    """API 中断后，换一个 AnalyzeService 实例仍能按 matter_id 从 checkpoint 恢复。"""
    test_client, _svc = client
    response = _upload(
        test_client,
        ROOT / "examples" / "sample_procurement_contract.pdf",
        stance="乙方",
    )
    assert response.status_code == 202
    record = response.json()
    task = _wait(test_client, record)
    assert task["status"] == "awaiting_review"
    matter_id = record["matter_id"]

    second = AnalyzeService(settings)
    monkeypatch.setattr(api_main, "service", second)
    headers = {"X-Matter-Token": record["token"]}
    resumed = test_client.post(f"/review/{matter_id}", json={"action": "approve"}, headers=headers)
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "completed"
    assert second.store.count_findings(matter_id) == resumed.json()["findings_count"]
    assert test_client.get(f"/export/{matter_id}", headers=headers).status_code == 200


def test_reject_keeps_checkpoint_without_export(client):
    test_client, _svc = client
    record = _upload(test_client, ROOT / "examples" / "sample_service_contract.docx").json()
    task = _wait(test_client, record)
    matter_id = task["matter_id"]
    headers = {"X-Matter-Token": record["token"]}
    rejected = test_client.post(
        f"/review/{matter_id}", json={"action": "reject", "comment": "API 驳回"}, headers=headers
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["docx_path"] == ""
    assert test_client.get(f"/export/{matter_id}", headers=headers).status_code == 409


def test_unknown_matter_returns_404(client):
    test_client, _svc = client
    assert test_client.post("/review/matter-nope", json={"action": "approve"}).status_code == 404
    assert test_client.get("/export/matter-nope").status_code == 404
    assert test_client.get("/tasks/task-nope").status_code == 404
