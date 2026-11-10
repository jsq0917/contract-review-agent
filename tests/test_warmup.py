"""精排预热的生命周期：跳过条件、就绪/失败状态。

预热是后台线程，测试只断言**状态机**与**是否启动**，不去加载真实模型
（真加载一次 20s，测试里不值得）。
"""

from __future__ import annotations

import dataclasses
import time

from lexflow.config import Settings
from lexflow.service import AnalyzeService


def _wait_status(service: AnalyzeService, expected: set[str], timeout: float = 60.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = service.warmup_state["status"]
        if status in expected:
            return status
        time.sleep(0.05)
    return service.warmup_state["status"]


def test_warmup_skipped_without_model(service: AnalyzeService):
    state = service.warmup_state
    assert state["status"] == "skipped"
    assert "未配置" in state["reason"]
    assert service.retriever.reranker.available is False


def test_warmup_skipped_when_disabled(settings: Settings, tmp_path):
    """模型目录存在但预热被关掉：不应起线程，也不该碰模型。"""
    fake_model = tmp_path / "fake-reranker"
    fake_model.mkdir()
    custom = dataclasses.replace(
        settings, reranker_model=str(fake_model), reranker_warmup=False
    )
    service = AnalyzeService(custom)
    try:
        state = service.warmup_state
        assert state["status"] == "skipped"
        assert "关闭" in state["reason"]
        assert service.retriever.reranker.available is False
    finally:
        service.store.close()


def test_warmup_fails_fast_on_broken_model_dir(settings: Settings, tmp_path):
    """模型目录存在但内容不对：预热要落到 failed 并留下原因，而不是卡在 running。"""
    fake_model = tmp_path / "broken-reranker"
    fake_model.mkdir()
    custom = dataclasses.replace(
        settings,
        reranker_model=str(fake_model),
        reranker_warmup=True,
        # 允许加载：service 里恒为 True，这里显式写出来避免依赖实现细节
    )
    service = AnalyzeService(custom)
    try:
        status = _wait_status(service, {"failed", "ready"})
        state = service.warmup_state
        assert status == "failed", f"预期 failed，实际 {status}（{state['reason']}）"
        assert state["reason"]
        assert state["seconds"] is not None
        assert service.retriever.reranker.available is False
    finally:
        service.store.close()
