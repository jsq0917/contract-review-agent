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
    warnings = " | ".join(item.value for item in app.warning)
    assert "草稿 · 待人工确认" in warnings
    assert "五闸通过 0 条 / 跳过 1 条（不计通过）" in warnings
    assert "仅 G4 未判定" in warnings
    captions = " | ".join(item.value for item in app.caption)
    assert "引用 1 条、未引用 0 条" in captions
    assert any("（草稿）" in item.value for item in app.subheader)
    assert not app.get("link_button")
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


def _rejected_matter(docx_path: str = "") -> dict:
    return {
        "matter_id": "matter-rejected",
        "status": "rejected",
        "engine": "mock",
        "elapsed_ms": 1000,
        "docx_path": docx_path,
        "report": {"doc_id": "doc-1", "metrics": {}, "findings": [], "verifications": []},
    }


def test_ui_rejected_says_resubmit_and_hides_export(app: AppTest):
    """驳回后不能承诺「可重新确认」，也不该给一个必然 409 的 docx 入口。"""
    app.session_state["matter"] = _rejected_matter()
    app.run()
    assert not list(app.exception)
    warnings = " | ".join(item.value for item in app.warning)
    assert "已驳回，本次不导出" in warnings
    assert "重新上传并提交" in warnings
    assert "重新确认" not in warnings
    assert not app.get("link_button")
    labels = [item.label for item in app.button]
    assert "确认并导出 docx" not in labels
    assert "驳回（不导出）" not in labels


def test_ui_rejected_shows_export_only_when_docx_exists(app: AppTest):
    """只有 docx_path 真有值时，才渲染下载入口。"""
    app.session_state["matter"] = _rejected_matter(docx_path="E:/tmp/report.docx")
    app.run()
    assert not list(app.exception)
    links = app.get("link_button")
    assert len(links) == 1
    assert "下载 docx" in links[0].label


def test_ui_rejected_offers_resubmit_button(app: AppTest):
    """驳回后必须有回到上传区的入口，不能把用户卡在死页面。"""
    app.session_state["matter"] = _rejected_matter()
    app.run()
    assert not list(app.exception)
    assert "重新上传合同" in [item.label for item in app.button]
    assert any("（已驳回）" in item.value for item in app.subheader)


def test_ui_completed_shows_export_and_hides_confirm(app: AppTest):
    """确认后：横幅转已确认、出现下载、确认/驳回消失。"""
    app.session_state["matter"] = {
        "matter_id": "matter-done",
        "status": "completed",
        "engine": "mock",
        "elapsed_ms": 1000,
        "docx_path": "E:/tmp/report.docx",
        "report": {"doc_id": "doc-1", "metrics": {}, "findings": [], "verifications": []},
    }
    app.run()
    assert not list(app.exception)
    messages = " | ".join(item.value for item in app.success)
    assert "已确认并导出" in messages
    assert any("（已确认）" in item.value for item in app.subheader)
    assert len(app.get("link_button")) == 1
    labels = [item.label for item in app.button]
    assert "确认并导出 docx" not in labels
    assert "驳回（不导出）" not in labels


def test_ui_failed_state_explains_interrupted(app: AppTest):
    """failed/interrupted 不提供复核与导出，且要解释重启会标 interrupted。"""
    app.session_state["matter"] = {
        "matter_id": "matter-broken",
        "status": "interrupted",
        "engine": "mock",
        "elapsed_ms": 0,
        "report": {"doc_id": "doc-1", "metrics": {}, "findings": [], "verifications": []},
    }
    app.run()
    assert not list(app.exception)
    warnings = " | ".join(item.value for item in app.warning)
    assert "任务未完成（interrupted）" in warnings
    assert "checkpoint 仍在" in warnings
    assert not app.get("link_button")
    labels = [item.label for item in app.button]
    assert "确认并导出 docx" not in labels
    assert "重新上传合同" in labels


def test_ui_submitting_flag_disables_review_buttons(app: AppTest):
    """submitting=True 时确认/驳回必须不可点，避免重复 POST /review。"""
    app.session_state["matter"] = {
        "matter_id": "matter-test",
        "status": "awaiting_review",
        "engine": "mock",
        "elapsed_ms": 1,
        "report": {"doc_id": "doc-1", "metrics": {}, "findings": [], "verifications": []},
    }
    app.session_state["submitting"] = True
    app.run()
    assert not list(app.exception)
    buttons = {item.label: item for item in app.button}
    assert buttons["确认并导出 docx"].disabled is True
    assert buttons["驳回（不导出）"].disabled is True


def test_ui_approve_posts_review_once(app: AppTest, monkeypatch: pytest.MonkeyPatch):
    """点一次确认只发一次 /review，且成功后切到已确认态。"""
    calls: list[str] = []

    class _Resp:
        status_code = 200
        text = ""

        @staticmethod
        def json() -> dict:
            return {"status": "completed", "docx_path": "E:/tmp/report.docx", "metrics": {}}

    def _post(url: str, **kwargs) -> _Resp:
        calls.append(url)
        return _Resp()

    monkeypatch.setattr("requests.post", _post)
    app.session_state["matter"] = {
        "matter_id": "matter-test",
        "status": "awaiting_review",
        "engine": "mock",
        "elapsed_ms": 1,
        "report": {"doc_id": "doc-1", "metrics": {}, "findings": [], "verifications": []},
    }
    app.run()
    buttons = {item.label: item for item in app.button}
    buttons["确认并导出 docx"].click()
    app.run()
    assert not list(app.exception)
    assert len(calls) == 1
    assert calls[0].endswith("/review/matter-test")
    assert app.session_state["matter"]["status"] == "completed"


def _matter_with_metrics(metrics: dict) -> dict:
    return {
        "matter_id": "matter-metrics",
        "status": "awaiting_review",
        "engine": "api",
        "elapsed_ms": 1000,
        "report": {"doc_id": "doc-1", "metrics": metrics, "findings": [], "verifications": []},
    }


def test_ui_rerank_cache_hits_count_as_effective(app: AppTest):
    """全部命中精排缓存不是降级：旧报告 rerank_used=false 也要按 cache_hits 说已生效。"""
    app.session_state["matter"] = _matter_with_metrics(
        {
            "rerank_requested": True,
            "rerank_used": False,
            "rerank_applied": 0,
            "rerank_cache_hits": 19,
            "rerank_fallback": 0,
            "rerank_reason": "未发起精排调用",
        }
    )
    app.run()
    assert not list(app.exception)
    captions = " | ".join(item.value for item in app.caption)
    assert "精排已生效，本次 19 次查询全部命中缓存" in captions
    assert not any("未生效" in item.value for item in app.warning)


def test_ui_rerank_fallback_still_reports_ineffective(app: AppTest):
    """真正降级（fallback>0）仍要报未生效，并给出原因。"""
    app.session_state["matter"] = _matter_with_metrics(
        {
            "rerank_requested": True,
            "rerank_used": False,
            "rerank_applied": 0,
            "rerank_cache_hits": 0,
            "rerank_fallback": 19,
            "rerank_reason": "未配置 reranker 模型",
        }
    )
    app.run()
    assert not list(app.exception)
    warnings = " | ".join(item.value for item in app.warning)
    assert "已请求精排但未生效" in warnings
    assert "未配置 reranker 模型" in warnings


def test_ui_rerank_disabled_shows_no_rerank_status(app: AppTest):
    """没请求精排时不渲染任何「已生效 / 未生效」结论。"""
    app.session_state["matter"] = _matter_with_metrics(
        {
            "rerank_requested": False,
            "rerank_used": False,
            "rerank_applied": 0,
            "rerank_cache_hits": 0,
            "rerank_fallback": 0,
        }
    )
    app.run()
    assert not list(app.exception)
    text = " | ".join(
        [item.value for item in app.caption] + [item.value for item in app.warning]
    )
    assert "本次未启用精排" in text
    assert "已生效" not in text


def test_ui_engine_choice_survives_rerun(app: AppTest):
    """engine 选择用 session_state 固定，提交后的 rerun 不再回落到 mock。

    下拉框在 form 内，按 Streamlit 语义只有提交时才落值；这里点「开始审查」
    （没有文件，不会真的发 /analyze）来提交表单，再验证后续 rerun 仍保持 api。
    """
    app.run()
    engine_box = next(item for item in app.selectbox if item.label == "engine")
    assert engine_box.value == "mock"
    engine_box.set_value("api")
    app.button[0].click()
    app.run()
    assert not list(app.exception)
    assert app.session_state["engine"] == "api"
    app.run()
    engine_box = next(item for item in app.selectbox if item.label == "engine")
    assert engine_box.value == "api"
