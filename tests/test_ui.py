"""Streamlit 单页的冒烟测试：用 AppTest 真跑脚本，不依赖真实后端。

README 里长期写着「UI 只做了代码层验证」——这里把开关渲染、表单提交语义、
以及精排提示的几种分支都用桩服务覆盖掉。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from lexflow.config import ROOT

APP = ROOT / "lexflow" / "ui" / "app.py"


@pytest.fixture()
def app(monkeypatch) -> AppTest:
    # 指向一个必然连不上的端口：让 /health 走「读不到」分支，用例不受本机 API 状态影响。
    monkeypatch.setenv("LEXFLOW_API_URL", "http://127.0.0.1:9")
    st.cache_data.clear()
    return AppTest.from_file(str(APP), default_timeout=30)


def test_ui_renders_rerank_toggle_without_backend(app: AppTest):
    app.run()
    assert not list(app.exception)
    toggles = [(item.label, item.value) for item in app.toggle]
    assert ("启用 rerank 精排", False) in toggles
    assert any("读不到 /health" in caption.value for caption in app.caption)


def test_ui_toggle_commits_on_form_submit(app: AppTest):
    """form 内的开关按 Streamlit 语义只在提交时提交值——不能写成“rerun 即生效”。

    这条是回归保护：以前用 ``value=...`` 每次 rerun 重新求值，用户打开的开关会在
    提交前被默认值覆盖回去；现在靠 session_state + key 保住。
    """
    app.run()
    app.toggle[0].set_value(True)
    app.button[0].click()
    app.run()
    assert not list(app.exception)
    assert app.session_state["rerank_enabled"] is True
    assert app.toggle[0].value is True


def test_ui_shows_gates_and_skipped_verdicts(app: AppTest):
    """报告区必须能看见五闸状态、引文与 skipped 说明——不能只显示风险文字。"""
    app.session_state["matter"] = {
        "matter_id": "matter-test",
        "status": "awaiting_review",
        "engine": "api",
        "elapsed_ms": 1000,
        "report": {
            "doc_id": "doc-1",
            "metrics": {},
            "findings": [
                {
                    "finding_id": "F-legal-C0001-1",
                    "clause_id": "C0001",
                    "risk_level": "high",
                    "reviewer": "legal",
                    "reviewers": ["legal"],
                    "risk_type": "payment",
                    "rule_id": None,
                    "issue": "付款期限过长",
                    "original_quote": "甲方应在收到发票后 90 日内付款。",
                    "suggestion": "缩短付款期限",
                    "law_name": "中华人民共和国民法典",
                    "article_no": "第五百八十五条",
                    "cited_quote": "当事人可以约定违约金。",
                    "citation_reason": None,
                }
            ],
            "verifications": [
                {
                    "finding_id": "F-legal-C0001-1",
                    "gate": gate,
                    "passed": gate not in ("G4", "G5"),
                    "status": "skipped" if gate in ("G4", "G5") else "pass",
                    "detail": "测试用核验说明",
                }
                for gate in ("G1", "G2", "G3", "G4", "G5")
            ],
        },
    }
    app.run()
    assert not list(app.exception)
    captions = " | ".join(item.value for item in app.caption)
    assert "五闸全过 0/1" in captions
    risk_table = app.dataframe[0].value.to_string()
    assert "G4–" in risk_table
    assert "当事人可以约定违约金。" in risk_table
    assert any("核验详情" in item.label for item in app.expander)


class _HealthHandler(BaseHTTPRequestHandler):
    payload: dict = {}

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定命名
        body = json.dumps(_HealthHandler.payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # 静音访问日志
        return


@pytest.fixture()
def stub_health(monkeypatch):
    """起一个只回 /health 的本地桩服务，用来驱动 UI 的精排提示分支。"""
    server = HTTPServer(("127.0.0.1", 0), _HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("LEXFLOW_API_URL", f"http://127.0.0.1:{server.server_port}")

        def apply(payload: dict) -> None:
            _HealthHandler.payload = payload
            st.cache_data.clear()

        yield apply
    finally:
        server.shutdown()
        server.server_close()


def _payload(**overrides) -> dict:
    base = {
        "status": "ok",
        "articles": 1465,
        "retrieval_mode": "hybrid",
        "embedding_device": "cpu",
        "reranker_configured": False,
        "reranker_model_found": True,
        "reranker_available": True,
        "reranker_device": "cuda",
        "reranker_reason": None,
        "reranker_warmup": "ready",
        "reranker_warmup_s": 25.3,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"reranker_warmup": "running", "reranker_available": False}, "正在后台预热"),
        ({}, "已预热"),
        ({"reranker_model_found": False, "reranker_available": False}, "未找到本地 reranker"),
        (
            {"reranker_warmup": "failed", "reranker_available": False, "reranker_reason": "加载失败"},
            "预热失败",
        ),
    ],
)
def test_ui_rerank_hint_branches(stub_health, overrides: dict, expected: str):
    stub_health(_payload(**overrides))
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    assert not list(at.exception)
    captions = " | ".join(item.value for item in at.caption)
    assert expected in captions, captions
