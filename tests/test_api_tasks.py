"""LF-N2：任务化 /analyze、状态查询、取消、有限重试与幂等键。"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

import lexflow.api.main as api_main
from lexflow.config import ROOT
from lexflow.service import AnalyzeService, RunResult

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


def _upload(client: TestClient, path, headers: dict | None = None, **form):
    with open(path, "rb") as handle:
        return client.post(
            "/analyze",
            files={"file": (getattr(path, "name", "contract.docx"), handle.read(), DOCX_MIME)},
            data={"stance": "甲方", "as_of": "2026-11-03", "engine": "mock", **form},
            headers=headers or {},
        )


def _wait(client: TestClient, record: dict, timeout: float = 30.0) -> dict:
    headers = {"X-Matter-Token": record["token"]}
    deadline = time.time() + timeout
    payload: dict = record
    while time.time() < deadline:
        response = client.get(f"/tasks/{record['task_id']}", headers=headers)
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] not in ("queued", "running", "cancelling"):
            return payload
        time.sleep(0.02)
    raise AssertionError(f"任务未在 {timeout}s 内结束: {payload}")


def _fake_result(matter_id: str) -> RunResult:
    return RunResult(
        matter_id=matter_id,
        status="awaiting_review",
        engine="mock",
        findings_count=0,
        docx_path=None,
        elapsed_ms=1,
        report={"findings": [], "metrics": {}},
        trace_id=f"trace-{matter_id}",
    )


def test_submit_returns_immediately_and_health_not_blocked(client, monkeypatch):
    test_client, svc = client
    gate = threading.Event()
    entered = threading.Event()

    def slow(file_path, **kwargs):
        entered.set()
        gate.wait(10)
        return _fake_result(kwargs["matter_id"])

    monkeypatch.setattr(svc, "analyze", slow)
    started = time.perf_counter()
    response = _upload(test_client, ROOT / "examples" / "sample_service_contract.docx")
    elapsed = time.perf_counter() - started
    assert response.status_code == 202, response.text
    assert elapsed < 5
    record = response.json()
    assert record["status"] == "queued"
    assert entered.wait(5), "后台任务没有启动"

    started = time.perf_counter()
    health = test_client.get("/health")
    assert health.status_code == 200
    assert time.perf_counter() - started < 5

    headers = {"X-Matter-Token": record["token"]}
    running = test_client.get(f"/tasks/{record['task_id']}", headers=headers).json()
    assert running["status"] == "running"
    assert running["progress"]["events"] >= 0

    gate.set()
    finished = _wait(test_client, record)
    assert finished["status"] == "awaiting_review"


def test_idempotency_key_reuses_same_task_and_cleans_replay_upload(client, monkeypatch):
    test_client, svc = client
    gate = threading.Event()

    def slow(file_path, **kwargs):
        gate.wait(10)
        return _fake_result(kwargs["matter_id"])

    monkeypatch.setattr(svc, "analyze", slow)
    path = ROOT / "examples" / "sample_service_contract.docx"
    first = _upload(test_client, path, headers={"Idempotency-Key": "same-key"})
    second = _upload(test_client, path, headers={"Idempotency-Key": "same-key"})
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["task_id"] == first.json()["task_id"]
    assert second.json()["idempotent_replay"] is True

    gate.set()
    _wait(test_client, first.json())
    uploads = list((svc.settings.data_dir / "uploads").glob("*"))
    assert uploads == [], f"临时上传文件未清理: {uploads}"


def test_cancel_blocks_review_and_export(client, monkeypatch):
    test_client, svc = client
    gate = threading.Event()
    entered = threading.Event()

    def slow(file_path, **kwargs):
        entered.set()
        gate.wait(10)
        return _fake_result(kwargs["matter_id"])

    monkeypatch.setattr(svc, "analyze", slow)
    response = _upload(test_client, ROOT / "examples" / "sample_service_contract.docx")
    assert response.status_code == 202
    record = response.json()
    headers = {"X-Matter-Token": record["token"]}
    assert entered.wait(5)

    cancelled = test_client.post(f"/tasks/{record['task_id']}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelling"

    gate.set()
    finished = _wait(test_client, record)
    assert finished["status"] == "cancelled"

    matter_id = record["matter_id"]
    assert (
        test_client.post(
            f"/review/{matter_id}", json={"action": "approve"}, headers=headers
        ).status_code
        == 409
    )
    assert test_client.get(f"/export/{matter_id}", headers=headers).status_code == 409


def test_failed_task_reports_public_error_and_retry_is_limited(client, monkeypatch):
    test_client, svc = client
    calls = {"n": 0}
    source_name = "sample_service_contract.docx"

    def flaky(file_path, **kwargs):
        # 模拟真实 analyze 把上传件复制进 matter 产物目录，供 retry 复用。
        art = svc.settings.artifacts_dir / kwargs["matter_id"]
        art.mkdir(parents=True, exist_ok=True)
        (art / source_name).write_bytes(b"demo")
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("upstream down at E:\\srv\\lexflow\\internal.py, key sk-abcdefgh12345678")
        return _fake_result(kwargs["matter_id"])

    monkeypatch.setattr(svc, "analyze", flaky)
    record = _upload(test_client, ROOT / "examples" / source_name).json()
    headers = {"X-Matter-Token": record["token"]}
    failed = _wait(test_client, record)
    assert failed["status"] == "failed"
    message = failed["error"]["message"]
    assert "E:\\srv" not in message
    assert "sk-abcdefgh12345678" not in message
    assert "<path>" in message and "sk-***" in message

    retried = test_client.post(f"/tasks/{record['task_id']}/retry", headers=headers)
    assert retried.status_code == 202, retried.text
    new_record = retried.json()
    assert new_record["task_id"] != record["task_id"]
    assert new_record["attempts"] == 2
    finished = _wait(test_client, new_record)
    assert finished["status"] == "awaiting_review"

    again = test_client.post(f"/tasks/{record['task_id']}/retry", headers=headers)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "retry_not_allowed"


def test_cancel_finished_task_is_conflict(client, monkeypatch):
    test_client, svc = client
    monkeypatch.setattr(svc, "analyze", lambda file_path, **kwargs: _fake_result(kwargs["matter_id"]))
    record = _upload(test_client, ROOT / "examples" / "sample_service_contract.docx").json()
    _wait(test_client, record)
    headers = {"X-Matter-Token": record["token"]}
    response = test_client.post(f"/tasks/{record['task_id']}/cancel", headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "task_not_cancellable"
