"""混合检索：BM25 + 可选本地 bge-m3 向量，RRF 融合；可选 bge-reranker。

降级分两种，口径不同：

- **终态 BM25-only**：embedding 依赖缺失 / 模型路径不存在 / 被显式禁用。重试没有意义，
  状态直接落到 ``bm25-only``，不再自愈。
- **可自愈的 degraded**：模型已加载成功过，但构建索引或查询时抛异常（显存不足、
  单次 forward 失败等）。先降级 BM25 顶住本次请求，冷却期过后在后台重建；
  连续失败超过 ``recover_retries`` 次才转为终态。

两种状态都不抛异常、都不谎报 ready：``vector_reason`` 记录原因，``health_snapshot``
额外给出 ``degraded`` / ``vector_failures`` / ``next_retry_in_s``。

**rerank 的唯一入口是 ``retrieve()``**：``search()`` 保持纯 BM25/向量/RRF，不掺精排；
``retrieve(rerank=True)`` 才走 hybrid topN → CrossEncoder。该路径按
``(query, top_k, candidate_k)`` 做单飞 + LRU 缓存：法务与商务两路用**完全相同**的条款
文本检索，同一份打分不该算两遍，并发时也只算一次（见 ``_cached_rerank``）。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
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
        recover_retries: int = 3,
        recover_cooldown_s: float = 30.0,
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
        # 索引状态：new → indexing → ready / degraded / bm25-only。
        # new/indexing 冷启动会阻塞首问；degraded 只打后台重建，不拖住当前请求。
        self._state = "new"
        self._load_lock = threading.RLock()
        self._load_thread: threading.Thread | None = None
        self._rerank_reason = ""
        # 自愈账本：连续失败次数、下次允许重试的时刻、上一次降级原因。
        self._recover_retries = max(0, int(recover_retries))
        self._recover_cooldown_s = max(0.0, float(recover_cooldown_s))
        self._failures = 0
        self._next_retry_at = 0.0
        self._degraded_reason = ""
        # rerank 缓存：语料与模型在进程内固定，同 key 的打分结果可复用。
        # _inflight 做单飞——并发请求同一个 key 时只算一次，其余等结果，避免两路审查各算一遍。
        self._cache_lock = threading.Lock()
        self._cache: OrderedDict[
            tuple[str, int, int], list[tuple[LawArticle, float]]
        ] = OrderedDict()
        self._inflight: dict[tuple[str, int, int], threading.Event] = {}
        self._cache_max = 256
        self._cache_wait_s = 120.0
        self._rerank_stats = {"calls": 0, "applied": 0, "fallback": 0, "cache_hits": 0}

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

    def _start_loading(self) -> tuple[threading.Thread | None, bool]:
        """保证同一时刻只有一个后台构建线程；返回 ``(线程, 是否应阻塞等待)``。

        冷启动（new/indexing）返回阻塞标记，避免首问静默走 BM25；
        degraded 只起后台重建、不阻塞，当前请求先用 BM25 顶上。
        """
        with self._load_lock:
            if self._state == "indexing":
                return self._load_thread, True
            if self._state in {"ready", "bm25-only"}:
                return self._load_thread, False
            if self._state == "degraded":
                if not self._retry_allowed():
                    return self._load_thread, False
                return self._spawn_locked(), False
            self._state = "indexing"
            return self._spawn_locked(), True

    def _spawn_locked(self) -> threading.Thread:
        self._state = "indexing"
        thread = threading.Thread(
            target=self._build_vectors,
            name="lexflow-vector-index",
            daemon=True,
        )
        self._load_thread = thread
        thread.start()
        return thread

    def _retry_allowed(self) -> bool:
        """自愈预算与冷却期都在锁内判定，避免并发请求同时触发重建。"""
        if self._failures > self._recover_retries:
            return False
        return time.monotonic() >= self._next_retry_at

    def _build_vectors(self) -> None:
        """后台构建向量索引；完成前 _state=indexing，完成后再切到 ready/degraded/bm25-only。"""
        try:
            self.embedder.load()
            if not self.embedder.available:
                # 依赖缺失/路径不存在/被禁用属于终态，重试没有意义。
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
            if not self._load_cache():
                import numpy as np

                texts = [
                    f"{article.law_name} {article.article_no} {article.text}"
                    for article in self.articles
                ]
                vectors = np.array(self.embedder.encode(texts), dtype="float32")
                with self._load_lock:
                    self._vectors = vectors
                    self.vector_backend = "inline"
                self._save_cache()
            self._build_faiss_index()
            self._mark_ready()
        except Exception as exc:
            # 模型本身可用、只是这一轮构建失败（显存/单次 forward 抖动），先降级再重试。
            self._degrade(f"向量索引构建失败: {type(exc).__name__}: {exc}", kind="build")

    def _mark_ready(self) -> None:
        with self._load_lock:
            recovered = bool(self._degraded_reason)
            self._state = "ready"
            self._failures = 0
            self._next_retry_at = 0.0
            self._degraded_reason = ""
            self.vector_reason = ""
            log_event(
                logger,
                "retrieval.vector_recovered" if recovered else "retrieval.vector_ready",
                mode="hybrid",
                backend=self.vector_backend,
                device=self.embedder.resolved_device,
                count=len(self.articles),
            )

    def _degrade(self, reason: str, *, kind: str = "query") -> None:
        """运行时/构建失败：先降级 BM25，冷却期后自愈；预算用尽才转终态。"""
        with self._load_lock:
            self._failures += 1
            self._degraded_reason = reason
            self._vectors = None
            self._faiss_index = None
            exhausted = self._failures > self._recover_retries
            if exhausted:
                # 预算用尽：保持 BM25-only，不再自动重试，等进程重启。
                self._state = "bm25-only"
                self._next_retry_at = 0.0
                self.vector_reason = (
                    f"{reason}（自愈 {self._recover_retries} 次用尽，保持 BM25-only 直到重启）"
                )
            else:
                self._state = "degraded"
                self._next_retry_at = time.monotonic() + self._recover_cooldown_s
                self.vector_reason = (
                    f"{reason}（第 {self._failures} 次，"
                    f"{self._recover_cooldown_s:.0f}s 后重试）"
                )
            log_event(
                logger,
                "retrieval.vector_degraded",
                mode="bm25-only",
                kind=kind,
                reason=self.vector_reason,
                failures=self._failures,
                retries_left=max(0, self._recover_retries - self._failures),
                next_retry_in_s=(
                    None
                    if exhausted
                    else round(max(0.0, self._next_retry_at - time.monotonic()), 1)
                ),
                model=self.embedder.model_name,
            )

    def _load_vectors(self, *, wait: bool = True) -> None:
        """冷启动（new/indexing）阻塞等待；自愈重建只在后台跑，不拖住当前请求。"""
        thread, blocking = self._start_loading()
        if wait and blocking and thread is not None and thread.is_alive():
            thread.join()

    def wait_ready(self, timeout: float | None = 30.0) -> bool:
        """阻塞到 ready 或终态，返回是否 ready。CLI / 批量评测 / 自愈等场景用。"""
        deadline = time.monotonic() + (timeout if timeout is not None else 3600.0)
        while True:
            thread, _blocking = self._start_loading()
            if thread is not None and thread.is_alive():
                thread.join(max(0.0, min(0.5, deadline - time.monotonic())))
            if self._state == "ready":
                return True
            if self._state == "bm25-only":
                return False
            if time.monotonic() >= deadline:
                return self.vector_ready
            time.sleep(0.05)

    def health_snapshot(self) -> dict[str, object]:
        """健康检查用的非阻塞快照：构建中返回 indexing、自愈中返回 bm25-only+degraded。"""
        self._start_loading()
        with self._load_lock:
            state = self._state
            vector_ready = state == "ready" and self._vectors is not None
            if state == "ready":
                mode = "hybrid"
            elif state in {"bm25-only", "degraded"}:
                # degraded 时确实在用 BM25 服务，不谎报 indexing。
                mode = "bm25-only"
            else:
                mode = "indexing"
            return {
                "retrieval_mode": mode,
                "vector_ready": vector_ready,
                "vector_backend": self.vector_backend or None,
                "vector_reason": self.vector_reason or None,
                "embedding_device": self.embedder.resolved_device or None,
                "degraded": state == "degraded",
                "vector_failures": self._failures,
                "next_retry_in_s": (
                    round(max(0.0, self._next_retry_at - time.monotonic()), 1)
                    if state == "degraded"
                    else None
                ),
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

    @property
    def rerank_stats(self) -> dict[str, int]:
        """进程内 rerank 计数：calls / applied / fallback / cache_hits。

        报告与 /health 靠它区分“开了开关”与“真的精排了”——降级不能静默。
        """
        with self._cache_lock:
            return dict(self._rerank_stats)

    def reset_rerank_stats(self) -> None:
        with self._cache_lock:
            for key in self._rerank_stats:
                self._rerank_stats[key] = 0

    def clear_retrieve_cache(self) -> None:
        """只清结果缓存；在途请求由各自的 Event 收尾，不在这里打断。"""
        with self._cache_lock:
            self._cache.clear()

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
                # 单次查询失败不等于永久失效：降级 + 冷却后后台重建（见 _degrade）。
                self._degrade(f"向量查询失败，已降级 BM25-only: {type(exc).__name__}: {exc}")

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

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        *,
        rerank: bool = False,
        candidate_k: int | None = None,
        query_vector: list[float] | None = None,
    ) -> list[tuple[LawArticle, float]]:
        """业务检索统一入口：``rerank=False`` 等价于 ``search()``，行为完全一致。

        ``rerank=True`` 时走 hybrid 取候选 → CrossEncoder 精排，并做两件事：

        1. **候选收敛**：默认只取 ``max(10, top_k*3)`` 个候选。精排成本正比于打分的
           对数，而生产 ``top_k`` 通常只有 3，沿用评测口径的 20 是白算 17 次。
        2. **单飞 + LRU**：同 ``(query, top_k, candidate_k)`` 只算一次；法务与商务两路
           用的是同一段条款文本，并发进来时后者等前者，不重复打分。

        降级路径的结果**不进缓存**——模型暂时不可用不该被固化成“以后都这样”。
        """
        if not rerank:
            return self.search(query, top_k)
        candidate_k = max(top_k, candidate_k or max(10, top_k * 3))
        key = (query, top_k, candidate_k)
        with self._cache_lock:
            self._rerank_stats["calls"] += 1
        return self._cached_rerank(
            key, lambda: self._rerank_hits(query, top_k, candidate_k, query_vector)
        )

    def _cached_rerank(
        self,
        key: tuple[str, int, int],
        compute: Callable[[], tuple[list[tuple[LawArticle, float]], bool]],
    ) -> list[tuple[LawArticle, float]]:
        """单飞 + LRU。等待超时后自己算，但不再登记，避免把正在算的那个顶掉。"""
        event: threading.Event | None = None
        owner = False
        deadline = time.monotonic() + self._cache_wait_s
        while True:
            with self._cache_lock:
                cached = self._cache.get(key)
                if cached is not None:
                    self._cache.move_to_end(key)
                    self._rerank_stats["cache_hits"] += 1
                    return cached
                waiting = self._inflight.get(key)
                if waiting is None:
                    event = threading.Event()
                    self._inflight[key] = event
                    owner = True
                    break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            waiting.wait(remaining)

        try:
            hits, ok = compute()
        except BaseException:
            if owner and event is not None:
                with self._cache_lock:
                    self._inflight.pop(key, None)
                event.set()
            raise
        with self._cache_lock:
            self._rerank_stats["applied" if ok else "fallback"] += 1
            if ok:
                self._cache[key] = hits
                self._cache.move_to_end(key)
                while len(self._cache) > self._cache_max:
                    self._cache.popitem(last=False)
            if owner and event is not None:
                self._inflight.pop(key, None)
        if owner and event is not None:
            event.set()
        return hits

    def _rerank_hits(
        self,
        query: str,
        top_k: int,
        candidate_k: int,
        query_vector: list[float] | None = None,
    ) -> tuple[list[tuple[LawArticle, float]], bool]:
        """返回 ``(hits, 是否真的精排成功)``；失败时给 hybrid 前 top_k 并记录原因。"""
        candidates = self.search(query, candidate_k, mode="hybrid", query_vector=query_vector)
        self._rerank_reason = ""
        self.reranker.load()
        if not self.reranker.available:
            self._rerank_reason = self.reranker.reason
            return candidates[:top_k], False
        try:
            return self.reranker.rerank(query, candidates, top_k), True
        except Exception as exc:
            self._rerank_reason = f"rerank 失败: {type(exc).__name__}: {exc}"
            return candidates[:top_k], False

    def search_reranked(
        self,
        query: str,
        top_k: int = 5,
        *,
        candidate_k: int = 20,
        query_vector: list[float] | None = None,
    ) -> list[tuple[LawArticle, float]]:
        """精排直调，不缓存、不收敛候选——评测脚本按显式 candidate_k 用这条路径。"""
        hits, _ok = self._rerank_hits(query, top_k, candidate_k, query_vector)
        return hits
