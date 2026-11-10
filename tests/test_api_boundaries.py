"""LF-O3：上传边界、服务端生成路径、临时文件清理、matter token 与错误脱敏。"""

from __future__ import annotations

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


def _upload_bytes(client: TestClient, name: str, payload: bytes, headers: dict | None = None):
    return client.post(
        "/analyze",
        files={"file": (name, payload, DOCX_MIME)},
        data={"stance": "甲方", "as_of": "2026-11-03", "engine": "mock"},
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


def test_rejects_unsupported_extension(client):
    test_client, _svc = client
    response = _upload_bytes(test_client, "malware.exe", b"MZ")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_file_type"


def test_rejects_empty_file_and_cleans_temp(client):
    test_client, svc = client
    response = _upload_bytes(test_client, "empty.docx", b"")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "empty_file"
    uploads = list((svc.settings.data_dir / "uploads").glob("*"))
    assert uploads == [], f"空文件路径残留临时文件: {uploads}"


def test_rejects_oversized_upload_and_cleans_temp(client, monkeypatch):
    test_client, svc = client
    monkeypatch.setattr(svc.settings, "max_upload_bytes", 1024)
    response = _upload_bytes(test_client, "big.docx", b"x" * 4096)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
    uploads = list((svc.settings.data_dir / "uploads").glob("*"))
    assert uploads == [], f"超限文件路径残留临时文件: {uploads}"


def test_client_filename_cannot_escape_artifacts(client, monkeypatch):
    test_client, svc = client

    def fake(file_path, **kwargs):
        return RunResult(
            matter_id=kwargs["matter_id"],
            status="awaiting_review",
            engine="mock",
            findings_count=0,
            docx_path=None,
            elapsed_ms=1,
            report={"findings": [], "metrics": {}},
            trace_id=f"trace-{kwargs['matter_id']}",
        )

    monkeypatch.setattr(svc, "analyze", fake)
    response = _upload_bytes(test_client, "..\\..\\evil.docx", b"demo")
    assert response.status_code == 202, response.text
    record = response.json()
    _wait(test_client, record)
    matter = svc.store.get_matter(record["matter_id"])
    assert matter is not None
    assert matter["filename"] == "evil.docx"
    assert ".." not in matter["filename"]
    assert not (svc.settings.data_dir / "evil.docx").exists()
    assert not (svc.settings.artifacts_dir.parent / "evil.docx").exists()


def test_matter_token_required_for_review_export_and_tasks(client, monkeypatch):
    test_client, svc = client
    monkeypatch.setattr(
        svc, "analyze", lambda file_path, **kwargs: RunResult(
            matter_id=kwargs["matter_id"],
            status="awaiting_review",
            engine="mock",
            findings_count=0,
            docx_path=None,
            elapsed_ms=1,
            report={"findings": [], "metrics": {}},
            trace_id=f"trace-{kwargs['matter_id']}",
        )
    )
    record = _upload_bytes(test_client, "contract.docx", b"demo").json()
    matter_id = record["matter_id"]
    _wait(test_client, record)

    assert test_client.get(f"/tasks/{record['task_id']}").status_code == 403
    assert test_client.get(f"/export/{matter_id}").status_code == 403
    assert (
        test_client.post(f"/review/{matter_id}", json={"action": "approve"}).status_code == 403
    )
    wrong = {"X-Matter-Token": "not-the-token"}
    assert test_client.get(f"/export/{matter_id}", headers=wrong).status_code == 403
    right = {"X-Matter-Token": record["token"]}
    # token 正确时放行到业务层：docx 还没生成，返回 409 而不是 403。
    assert test_client.get(f"/export/{matter_id}", headers=right).status_code == 409
    # 导出链接支持 ?token=（浏览器下载无法带自定义 header）。
    assert (
        test_client.get(f"/export/{matter_id}", params={"token": record["token"]}).status_code
        == 409
    )


def test_temp_upload_cleaned_after_success(client, monkeypatch):
    test_client, svc = client
    monkeypatch.setattr(
        svc,
        "analyze",
        lambda file_path, **kwargs: RunResult(
            matter_id=kwargs["matter_id"],
            status="awaiting_review",
            engine="mock",
            findings_count=0,
            docx_path=None,
            elapsed_ms=1,
            report={"findings": [], "metrics": {}},
            trace_id=f"trace-{kwargs['matter_id']}",
        ),
    )
    record = _upload_bytes(
        test_client, "sample_service_contract.docx",
        (ROOT / "examples" / "sample_service_contract.docx").read_bytes(),
    ).json()
    _wait(test_client, record)
    uploads = list((svc.settings.data_dir / "uploads").glob("*"))
    assert uploads == [], f"成功路径残留临时文件: {uploads}"


def test_unhandled_error_returns_generic_body(client, monkeypatch):
    test_client, svc = client

    def boom(file_path, **kwargs):
        raise ValueError("secret at /srv/lexflow/internal.py")

    monkeypatch.setattr(svc, "analyze", boom)
    record = _upload_bytes(test_client, "contract.docx", b"demo").json()
    failed = _wait(test_client, record)
    assert failed["status"] == "failed"
    assert "error" in failed
    assert "/srv/lexflow" not in str(failed["error"])
    # 客户端只看到脱敏后的短消息，服务端日志保留完整原因。
    assert failed["error"]["message"]
