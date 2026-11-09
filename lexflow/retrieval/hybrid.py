"""混合检索：BM25 + 可选本地 bge-m3 向量，RRF 融合；可选 bge-reranker。

向量层不可用（依赖缺失、模型路径不存在、加载失败）时退化为 BM25-only，
mode 返回 bm25-only，并通过 vector_reason 说明原因，不抛异常。
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from lexflow.logging import get_logger, log_event
from lexflow.retrieval.bm25 import BM25
from lexflow.retrieval.corpus import LawArticle
from lexflow.retrieval.embedder import LocalEmbedder
from lexflow.retrieval.reranker import LocalReranker

MODES = ("bm25", "dense", "hybrid")
logger = get_logger("lexflow.retrieval")


class HybridRetriever:
    def __init__(
        self,
        articles: list[LawArticle],
        embedding_model: str = "",
        *,
        embedding_enabled: bool = True,
        embedding_device: str = "auto",
        cache_dir: str | Path | None = None,
        reranker_model: str = "",
        reranker_enabled: bool = False,
        reranker_device: str = "auto",
    ) -> None:
        self.articles = articles
        self.bm25 = BM25([article.text for article in articles])
        self.embedder = LocalEmbedder(
            embedding_model, device=embedding_device, enabled=embedding_enabled
        )
        self.reranker = LocalReranker(
            reranker_model, device=reranker_device, enabled=reranker_enabled
        )
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._vectors = None
        self._faiss_index = None
        self.vector_reason = ""
        self.vector_backend = ""
        # 索引状态：new / indexing / ready / bm25-only。
        # 后台线程负责加载，mode/search 会等待线程结束，避免把“正在建索引”谎报为 bm25-only。
        self._state = "new"
        self._load_lock = threading.RLock()
        self._load_thread: threading.Thread | None = None
        self._rerank_reason = ""

    # ---------- 向量加载与缓存 ----------

    def _cache_key(self) -> str:
        payload = "|".join(
            f"{article.law_name}#{article.article_no}#{article.content_hash or article.text}"
            for article in self.articles
        )
        raw = f"{self.embedder.model_name}|{len(self.articles)}|{payload}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _cache_paths(self) -> tuple[Path, Path] | None:
        if self.cache_dir is None:
            return None
        key = self._cache_key()
        return self.cache_dir / f"{key}.npy", self.cache_dir / f"{key}.json"

    def _load_cache(self) -> bool:
        paths = self._cache_paths()
        if paths is None:
            return False
        vector_path, meta_path = paths
        if not vector_path.exists() or not meta_path.exists():
            return False
        try:
            import numpy as np

            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("count") != len(self.articles) or meta.get("model") != self.embedder.model_name:
                return False
            vectors = np.load(vector_path)
            if vectors.shape[0] != len(self.articles):
                return False
            self._vectors = vectors.astype("float32")
            self.vector_backend = "cache"
            return True
        except Exception:
            return False

    def _save_cache(self) -> None:
        paths = self._cache_paths()
        if paths is None or self._vectors is None:
            return
        vector_path, meta_path = paths
        try:
            import numpy as np

            self.cache_dir.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
            np.save(vector_path, self._vectors)
            meta_path.write_text(
                json.dumps(
                    {
                        "model": self.embedder.model_name,
                        "count": len(self.articles),
                        "dim": int(self._vectors.shape[1]),
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
        except Exception:
            pass

    def _start_loading(self) -> threading.Thread | None:
        """保证首次加载只启动一个后台线程，其余调用共享该线程。"""
        with self._load_lock:
            if self._state in {"ready", "bm25-only"}:
                return self._load_thread
            if self._state == "new":
                self._state = "indexing"
                thread = threading.Thread(
                    target=self._build_vectors,
                    name="lexflow-vector-index",
                    daemon=True,
                )
                self._load_thread = thread
                thread.start()
            return self._load_thread

    def _build_vectors(self) -> None:
        """后台构建向量索引；完成前 _state=indexing，完成后再切换到 ready/bm25-only。"""
        try:
            self.embedder.load()
            if not self.embedder.available:
                with self._load_lock:
                    self.vector_reason = self.embedder.reason
                    self._state = "bm25-only"
                log_event(
                    logger,
                    "retrieval.vector_unavailable",
                    mode="bm25-only",
                    reason=self.vector_reason,
                    model=self.embedder.model_name,
                )
                return
            if self._load_cache():
                self._build_faiss_index()
                with self._load_lock:
                    self._state = "ready"
                log_event(
                    logger,
                    "retrieval.vector_ready",
                    mode="hybrid",
                    backend=self.vector_backend,
                    device=self.embedder.resolved_device,
                    count=len(self.articles),
                )
                return
            import numpy as np

            texts = [
                f"{article.law_name} {article.article_no} {article.text}" for article in self.articles
            ]
            vectors = np.array(self.embedder.encode(texts), dtype="float32")
            with self._load_lock:
                self._vectors = vectors
                self.vector_backend = "inline"
            self._save_cache()
            self._build_faiss_index()
            with self._load_lock:
                self._state = "ready"
            log_event(
                logger,
                "retrieval.vector_ready",
                mode="hybrid",
                backend=self.vector_backend,
                device=self.embedder.resolved_device,
                count=len(self.articles),
            )
        except Exception as exc:
            with self._load_lock:
                self.vector_reason = f"向量索引构建失败: {type(exc).__name__}: {exc}"
                self._vectors = None
                self._faiss_index = None
                self._state = "bm25-only"
            log_event(
                logger,
                "retrieval.vector_unavailable",
                mode="bm25-only",
                reason=self.vector_reason,
                model=self.embedder.model_name,
            )

    def _load_vectors(self) -> None:
        """阻塞到首次索引构建结束；并发调用等待同一后台线程，不会提前返回。"""
        thread = self._start_loading()
        if thread is not None and thread.is_alive():
            thread.join()

    def health_snapshot(self) -> dict[str, object]:
        """健康检查用的非阻塞快照：构建中返回 indexing，而不是 bm25-only。"""
        self._start_loading()
        with self._load_lock:
            state = self._state
            vector_ready = state == "ready" and self._vectors is not None
            if state == "ready":
                mode = "hybrid"
            elif state == "bm25-only":
                mode = "bm25-only"
            else:
                mode = "indexing"
            return {
                "retrieval_mode": mode,
                "vector_ready": vector_ready,
                "vector_backend": self.vector_backend or None,
                "vector_reason": self.vector_reason or None,
                "embedding_device": self.embedder.resolved_device or None,
            }

    def _build_faiss_index(self) -> None:
        if self._vectors is None:
            return
        try:
            import faiss  # type: ignore

            index = faiss.IndexFlatIP(self._vectors.shape[1])
            index.add(self._vectors)
            self._faiss_index = index
        except ImportError:
            self._faiss_index = None

    # ---------- 对外属性 ----------

    @property
    def mode(self) -> str:
        self._load_vectors()
        return "hybrid" if self._vectors is not None else "bm25-only"

    @property
    def vectors_available(self) -> bool:
        self._load_vectors()
        return self._vectors is not None

    @property
    def vector_ready(self) -> bool:
        with self._load_lock:
            return self._state == "ready" and self._vectors is not None

    @property
    def rerank_reason(self) -> str:
        return self._rerank_reason

    # ---------- 检索 ----------

    def encode_queries(self, texts: list[str]) -> list[list[float]]:
        self._load_vectors()
        return self.embedder.encode(texts)

    def vector_search_by_vector(self, query_vector: list[float], top_k: int) -> list[tuple[int, float]]:
        if self._vectors is None:
            return []
        import numpy as np

        vector = np.array(query_vector, dtype="float32")
        if self._faiss_index is not None:
            scores, indices = self._faiss_index.search(vector.reshape(1, -1), top_k)
            return [(int(index), float(score)) for index, score in zip(indices[0], scores[0]) if index >= 0]
        scores = self._vectors @ vector
        order = np.argsort(-scores)[:top_k]
        return [(int(index), float(scores[index])) for index in order]

    def bm25_search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        return self.bm25.search(query, top_k=top_k)

    def search(
        self,
        query: str,
        top_k: int = 5,
        *,
        mode: str = "hybrid",
        query_vector: list[float] | None = None,
    ) -> list[tuple[LawArticle, float]]:
        if mode not in MODES:
            raise ValueError(f"未知检索模式: {mode}")
        self._load_vectors()
        bm25_hits = self.bm25_search(query, top_k)
        if mode == "bm25":
            return [(self.articles[index], score) for index, score in bm25_hits]

        vector_hits: list[tuple[int, float]] = []
        if self._vectors is not None:
            try:
                if query_vector is None:
                    query_vector = self.embedder.encode([query])[0]
                vector_hits = self.vector_search_by_vector(query_vector, top_k)
            except Exception as exc:
                self.vector_reason = f"向量查询失败，已降级 BM25-only: {type(exc).__name__}"
                self._vectors = None

        if mode == "dense":
            return [(self.articles[index], score) for index, score in vector_hits]
        if not vector_hits:
            return [(self.articles[index], score) for index, score in bm25_hits]

        # RRF：两路各自排名，score = sum(1/(60+rank))
        fused: dict[int, float] = {}
        for rank, (index, _score) in enumerate(bm25_hits):
            fused[index] = fused.get(index, 0.0) + 1.0 / (60 + rank + 1)
        for rank, (index, _score) in enumerate(vector_hits):
            fused[index] = fused.get(index, 0.0) + 1.0 / (60 + rank + 1)
        ranked = sorted(fused.items(), key=lambda item: item[1], reverse=True)[:top_k]
        return [(self.articles[index], score) for index, score in ranked]

    def search_reranked(
        self,
        query: str,
        top_k: int = 5,
        *,
        candidate_k: int = 20,
        query_vector: list[float] | None = None,
    ) -> list[tuple[LawArticle, float]]:
        candidates = self.search(query, candidate_k, mode="hybrid", query_vector=query_vector)
        self.reranker.load()
        if not self.reranker.available:
            self._rerank_reason = self.reranker.reason
            return candidates[:top_k]
        try:
            return self.reranker.rerank(query, candidates, top_k)
        except Exception as exc:
            self._rerank_reason = f"rerank 失败: {type(exc).__name__}: {exc}"
            return candidates[:top_k]
