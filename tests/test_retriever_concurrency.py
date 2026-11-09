"""首次索引加载与 /health 快照的并发回归。

目标：任何并发调用都不能在索引仍在构建时把 retrieval_mode 报成 bm25-only。
"""

from __future__ import annotations

import threading
import time

from lexflow.config import ROOT
from lexflow.retrieval.corpus import load_corpus
from lexflow.retrieval.hybrid import HybridRetriever

CORPUS_FILE = ROOT / "data" / "corpus" / "law_articles.jsonl"


class SlowEmbedder:
    """模拟本地模型加载 + 编码耗时；不依赖 sentence-transformers。"""

    def __init__(self, model_name: str, *, device: str = "auto", enabled: bool = True, batch_size: int = 16):
        self.model_name = model_name
        self.device = device
        self.enabled = enabled
        self.batch_size = batch_size
        self._model = None
        self.reason = ""
        self.resolved_device = ""

    @property
    def available(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        time.sleep(0.3)
        self._model = object()
        self.resolved_device = "cpu"

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, float(index % 7)] for index, _ in enumerate(texts)]


def test_concurrent_mode_and_health_never_report_bm25_during_indexing(monkeypatch) -> None:
    monkeypatch.setattr("lexflow.retrieval.hybrid.LocalEmbedder", SlowEmbedder)
    articles, _mode = load_corpus(CORPUS_FILE)
    retriever = HybridRetriever(
        articles[:50],
        embedding_model="slow-fake",
        embedding_enabled=True,
        cache_dir=None,
    )

    snapshots: list[dict[str, object]] = []
    modes: list[str] = []
    stop = threading.Event()
    barrier = threading.Barrier(8)

    def poll_health() -> None:
        while not stop.is_set():
            snapshots.append(dict(retriever.health_snapshot()))
            time.sleep(0.01)

    def read_mode() -> None:
        barrier.wait()
        modes.append(retriever.mode)

    pollers = [threading.Thread(target=poll_health) for _ in range(2)]
    readers = [threading.Thread(target=read_mode) for _ in range(8)]
    for thread in pollers + readers:
        thread.start()
    for thread in readers:
        thread.join()
    # 读取线程结束后索引已 ready；此时 health 快照必须报 hybrid。
    final_snapshot = retriever.health_snapshot()
    stop.set()
    for thread in pollers:
        thread.join()

    # 首轮 health 快照应明确返回 indexing，而不是 bm25-only。
    assert snapshots
    assert snapshots[0]["retrieval_mode"] == "indexing"
    assert snapshots[0]["vector_ready"] is False
    # 构建期间任何一次快照都不能谎报 bm25-only。
    assert all(item["retrieval_mode"] != "bm25-only" for item in snapshots)
    assert final_snapshot["retrieval_mode"] == "hybrid"
    assert final_snapshot["vector_ready"] is True

    # 并发的阻塞调用最终都看到 hybrid，没有一个提前返回 bm25-only。
    assert modes == ["hybrid"] * 8
    assert retriever.mode == "hybrid"
    assert retriever.vector_ready is True
