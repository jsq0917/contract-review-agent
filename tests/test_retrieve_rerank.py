"""retrieve() 的 rerank 分支：候选收敛、单飞缓存、降级不缓存。

这些用例全部用替身 reranker，不加载真实 CrossEncoder——跑得快，也不依赖模型文件。
断言的是**调用次数与降级语义**，不是重排质量（质量看 eval/run_retrieval_eval.py）。
"""

from __future__ import annotations

import threading
import time
from functools import lru_cache
from typing import Any

from lexflow.config import ROOT
from lexflow.retrieval.corpus import LawArticle, load_corpus
from lexflow.retrieval.hybrid import HybridRetriever

CORPUS_FILE = ROOT / "data" / "corpus" / "law_articles.jsonl"


@lru_cache(maxsize=1)
def _articles(limit: int = 60) -> tuple[LawArticle, ...]:
    articles, _mode = load_corpus(CORPUS_FILE)
    return tuple(articles[:limit])


class _StubReranker:
    """替身 CrossEncoder：只记账，不推理。"""

    def __init__(
        self, *, available: bool = True, delay: float = 0.0, fail_times: int = 0
    ) -> None:
        self.reason = "" if available else "stub reranker 不可用"
        self._available = available
        self.delay = delay
        self.fail_times = fail_times
        self.calls = 0
        self.candidates: list[int] = []
        self._lock = threading.Lock()

    def load(self) -> None:
        return None

    @property
    def available(self) -> bool:
        return self._available

    def rerank(
        self, query: str, candidates: list[tuple[LawArticle, float]], top_k: int
    ) -> list[tuple[LawArticle, float]]:
        with self._lock:
            self.calls += 1
            self.candidates.append(len(candidates))
            should_fail = self.fail_times > 0
            if should_fail:
                self.fail_times -= 1
        if self.delay:
            time.sleep(self.delay)
        if should_fail:
            raise RuntimeError("stub rerank 失败")
        return sorted(candidates, key=lambda item: -item[1])[:top_k]


def _retriever(**kwargs: Any) -> HybridRetriever:
    """BM25-only 检索器：不加载 embedding，让用例聚焦在 rerank 分支。"""
    return HybridRetriever(
        list(_articles()), "disabled", embedding_enabled=False, cache_dir=None, **kwargs
    )


def _with_stub(retriever: HybridRetriever, stub: _StubReranker) -> _StubReranker:
    retriever.reranker = stub  # type: ignore[assignment]
    return stub


def test_retrieve_without_rerank_matches_search():
    retriever = _retriever()
    assert retriever.retrieve("民事主体", 5) == retriever.search("民事主体", 5)


def test_candidate_k_is_capped_by_top_k():
    """生产 top_k=3，候选只需 10 个；沿用评测的 20 会白算 17 次打分。"""
    retriever = _retriever()
    stub = _with_stub(retriever, _StubReranker())

    retriever.retrieve("民事主体", 3, rerank=True)
    assert stub.candidates == [10]

    retriever.retrieve("民事主体", 8, rerank=True)
    assert stub.candidates[-1] == 24  # max(10, 8*3)

    retriever.retrieve("民事主体", 3, rerank=True, candidate_k=5)
    assert stub.candidates[-1] == 5


def test_same_query_is_scored_once():
    """法务与商务两路用同一段条款文本检索，第二次必须命中缓存。"""
    retriever = _retriever()
    stub = _with_stub(retriever, _StubReranker())

    first = retriever.retrieve("民事主体", 3, rerank=True)
    second = retriever.retrieve("民事主体", 3, rerank=True)

    assert stub.calls == 1
    assert first == second
    assert retriever.rerank_stats == {
        "calls": 2,
        "applied": 1,
        "fallback": 0,
        "cache_hits": 1,
    }


def test_concurrent_same_query_is_single_flight():
    """并发进来时不能各自算一遍——否则并行两路审查会把开销翻倍。"""
    retriever = _retriever()
    stub = _with_stub(retriever, _StubReranker(delay=0.2))

    workers = 6
    barrier = threading.Barrier(workers)
    results: list[list[tuple[LawArticle, float]]] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=5)
            results.append(retriever.retrieve("民事主体", 3, rerank=True))
        except BaseException as exc:  # noqa: BLE001 - 线程内异常要带回主线程断言
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert len(results) == workers
    assert stub.calls == 1
    assert all(result == results[0] for result in results)


def test_unavailable_rerank_falls_back_without_caching():
    """模型不可用时退回 hybrid 前 top_k，且不把降级结果固化进缓存。"""
    retriever = _retriever()
    stub = _with_stub(retriever, _StubReranker(available=False))

    first = retriever.retrieve("民事主体", 3, rerank=True)
    second = retriever.retrieve("民事主体", 3, rerank=True)

    assert first == retriever.search("民事主体", 3)
    assert first == second
    assert stub.calls == 0
    assert retriever.rerank_stats["fallback"] == 2
    assert retriever.rerank_stats["cache_hits"] == 0
    assert "不可用" in retriever.rerank_reason


def test_failed_rerank_retries_instead_of_reusing_fallback():
    """一次打分失败不能污染后续调用：下次仍要真的重排。"""
    retriever = _retriever()
    stub = _with_stub(retriever, _StubReranker(fail_times=1))

    retriever.retrieve("民事主体", 3, rerank=True)
    assert "rerank 失败" in retriever.rerank_reason

    hits = retriever.retrieve("民事主体", 3, rerank=True)
    assert hits
    assert stub.calls == 2
    assert retriever.rerank_stats["fallback"] == 1
    assert retriever.rerank_stats["applied"] == 1
    assert retriever.rerank_stats["cache_hits"] == 0


def test_search_reranked_bypasses_cache():
    """评测脚本走 search_reranked：显式 candidate_k、不缓存，口径保持原样。"""
    retriever = _retriever()
    stub = _with_stub(retriever, _StubReranker())

    retriever.search_reranked("民事主体", 3, candidate_k=20)
    retriever.search_reranked("民事主体", 3, candidate_k=20)

    assert stub.candidates == [20, 20]
    assert retriever.rerank_stats["cache_hits"] == 0


def _service_with_rerank_stats(stats: dict[str, int], reason: str = ""):
    """只借用 _rerank_metrics 的判定逻辑，不构造真实 Service（那会加载语料与模型）。"""
    from types import SimpleNamespace

    from lexflow.service import AnalyzeService

    service = AnalyzeService.__new__(AnalyzeService)
    service.retriever = SimpleNamespace(rerank_stats=stats, rerank_reason=reason)
    return service


def test_rerank_metrics_cache_only_counts_as_used():
    """纯缓存命中用的是此前的精排结果，不能算降级。"""
    service = _service_with_rerank_stats({"applied": 0, "fallback": 0, "cache_hits": 19})
    metrics = service._rerank_metrics(True, {"applied": 0, "fallback": 0, "cache_hits": 0})
    assert metrics["rerank_used"] is True
    assert metrics["rerank_applied"] == 0
    assert metrics["rerank_cache_hits"] == 19
    assert metrics["rerank_reason"] is None


def test_rerank_metrics_fallback_is_still_ineffective():
    """模型不可用/打分失败（fallback>0）才是真的未生效，且要带原因。"""
    service = _service_with_rerank_stats(
        {"applied": 0, "fallback": 16, "cache_hits": 0}, "未配置 reranker 模型"
    )
    metrics = service._rerank_metrics(True, {"applied": 0, "fallback": 0, "cache_hits": 0})
    assert metrics["rerank_used"] is False
    assert metrics["rerank_fallback"] == 16
    assert metrics["rerank_reason"] == "未配置 reranker 模型"


def test_rerank_metrics_applied_is_used_and_not_requested_has_no_reason():
    service = _service_with_rerank_stats({"applied": 10, "fallback": 0, "cache_hits": 14})
    metrics = service._rerank_metrics(True, {"applied": 0, "fallback": 0, "cache_hits": 0})
    assert metrics["rerank_used"] is True
    assert metrics["rerank_applied"] == 10

    skipped = service._rerank_metrics(False, {"applied": 10, "fallback": 0, "cache_hits": 14})
    assert skipped["rerank_requested"] is False
    assert skipped["rerank_reason"] is None
