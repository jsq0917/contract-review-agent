"""向量层自愈回归：运行时失败要能降级、冷却后重建、预算用尽才转终态。

三条口径必须同时成立，否则「降级」会退化成「静默永久损坏」：
1. 查询抛异常不向上冒，本次请求由 BM25 兜底；
2. 冷却期过后能在后台重建回 hybrid，不是等到进程重启；
3. 依赖本身缺失仍是终态，不会让 /health 在 indexing 与 bm25-only 之间来回翻。
"""

from __future__ import annotations

from lexflow.config import ROOT
from lexflow.retrieval.corpus import load_corpus
from lexflow.retrieval.hybrid import HybridRetriever

CORPUS_FILE = ROOT / "data" / "corpus" / "law_articles.jsonl"


class FakeEmbedder:
    """固定 2 维、加载即就绪；只关心降级/自愈的状态机，不关心检索质量。"""

    def __init__(
        self, model_name: str, *, device: str = "auto", enabled: bool = True, batch_size: int = 16
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.enabled = enabled
        self.batch_size = batch_size
        self._model = object() if enabled else None
        self.reason = "" if enabled else "embedding 已禁用"
        self.resolved_device = "cpu" if enabled else ""

    @property
    def available(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        return

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.5] for _ in texts]


def _make_retriever(monkeypatch, **kwargs) -> HybridRetriever:
    monkeypatch.setattr("lexflow.retrieval.hybrid.LocalEmbedder", FakeEmbedder)
    articles, _mode = load_corpus(CORPUS_FILE)
    return HybridRetriever(
        articles[:50],
        embedding_model="fake-embed",
        embedding_enabled=True,
        cache_dir=None,
        **kwargs,
    )


def _query(retriever: HybridRetriever) -> str:
    """用第一条法条的开头当查询，保证 BM25 一定有命中（不依赖具体语料内容）。"""
    return retriever.articles[0].text[:30]


def _boom(_query_vector, _top_k):
    raise RuntimeError("CUDA out of memory (simulated)")


def test_runtime_query_failure_degrades_then_self_heals(monkeypatch) -> None:
    retriever = _make_retriever(monkeypatch, recover_retries=2, recover_cooldown_s=0.3)
    assert retriever.wait_ready(timeout=10.0) is True
    assert retriever.mode == "hybrid"

    original = retriever.vector_search_by_vector
    flaky = {"fail": True}

    def maybe_fail(query_vector, top_k):
        if flaky["fail"]:
            raise RuntimeError("CUDA out of memory (simulated)")
        return original(query_vector, top_k)

    monkeypatch.setattr(retriever, "vector_search_by_vector", maybe_fail)

    hits = retriever.search(_query(retriever), top_k=3)
    assert hits, "向量失败时仍要由 BM25 兜底返回结果"
    assert retriever.vector_ready is False

    snap = retriever.health_snapshot()
    assert snap["retrieval_mode"] == "bm25-only"
    assert snap["degraded"] is True
    assert snap["vector_failures"] == 1
    assert float(snap["next_retry_in_s"]) > 0
    assert "CUDA out of memory" in str(snap["vector_reason"])

    # 冷却期过后允许重建，恢复后回到 hybrid。
    flaky["fail"] = False
    assert retriever.wait_ready(timeout=10.0) is True
    assert retriever.mode == "hybrid"

    after = retriever.health_snapshot()
    assert after["degraded"] is False
    assert after["vector_failures"] == 0
    assert after["vector_ready"] is True
    assert after["next_retry_in_s"] is None


def test_exhausted_retries_fall_back_to_terminal_bm25(monkeypatch) -> None:
    retriever = _make_retriever(monkeypatch, recover_retries=0, recover_cooldown_s=0.0)
    assert retriever.wait_ready(timeout=10.0) is True

    monkeypatch.setattr(retriever, "vector_search_by_vector", _boom)
    assert retriever.search(_query(retriever), top_k=3)

    snap = retriever.health_snapshot()
    assert snap["retrieval_mode"] == "bm25-only"
    assert snap["degraded"] is False, "预算用尽后是终态，不再标记为可自愈"
    assert "用尽" in str(snap["vector_reason"])

    # 终态不再自动重建：等再久也不会回到 indexing。
    assert retriever.wait_ready(timeout=1.0) is False
    assert retriever.health_snapshot()["retrieval_mode"] == "bm25-only"


def test_cooldown_blocks_immediate_rebuild(monkeypatch) -> None:
    retriever = _make_retriever(monkeypatch, recover_retries=3, recover_cooldown_s=60.0)
    assert retriever.wait_ready(timeout=10.0) is True

    monkeypatch.setattr(retriever, "vector_search_by_vector", _boom)
    assert retriever.search(_query(retriever), top_k=3)

    snap = retriever.health_snapshot()
    assert snap["degraded"] is True
    assert float(snap["next_retry_in_s"]) > 0

    # 冷却期内既不重建、也不谎报就绪。
    assert retriever.wait_ready(timeout=0.3) is False
    assert retriever.health_snapshot()["degraded"] is True


def test_missing_model_path_stays_terminal_without_retry() -> None:
    """依赖缺失属于终态：不进入 degraded，避免 /health 反复抖动。"""
    articles, _mode = load_corpus(CORPUS_FILE)
    retriever = HybridRetriever(
        articles[:20],
        embedding_model=str(ROOT / "models" / "does-not-exist"),
        embedding_enabled=True,
        cache_dir=None,
    )
    assert retriever.wait_ready(timeout=10.0) is False
    assert retriever.mode == "bm25-only"

    snap = retriever.health_snapshot()
    assert snap["degraded"] is False
    assert snap["vector_failures"] == 0
    assert "路径不存在" in str(snap["vector_reason"])
