"""本地 reranker（默认指向 E:\\project\\模型\\bge-reranker-v2-m3）。

只用于评测与显式开启的场景；不可用时返回明确原因，不影响 hybrid 检索。
"""

from __future__ import annotations

import threading
from pathlib import Path

from lexflow.devices import resolve_device
from lexflow.retrieval.corpus import LawArticle


class LocalReranker:
    def __init__(self, model_name: str, *, device: str = "auto", enabled: bool = True) -> None:
        self.model_name = model_name
        self.device = device
        self.enabled = enabled
        self._model = None
        self.reason = ""
        self.resolved_device = ""
        # 启动预热线程和请求线程可能同时进来：没有这把锁会加载两份模型，
        # GPU 上等于凭空多占 2 GB 显存。
        self._load_lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self._model is not None

    @property
    def model_present(self) -> bool:
        """模型是否**可解析**，不加载权重。

        ``/health`` 靠它回答“能不能开精排”，而不必付一次 15s 的加载代价。
        非本地路径（HF 仓库名）按“可解析”处理，真实可用性要等 ``load()``。
        """
        if not self.model_name:
            return False
        path = Path(self.model_name)
        return path.is_dir() or not path.is_absolute()

    def load(self) -> None:
        """幂等 + 线程安全：并发调用只真正加载一次，其余等待后直接返回。"""
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            self._load_locked()

    def _load_locked(self) -> None:
        if not self.enabled:
            self.reason = "reranker 未启用"
            return
        if not self.model_name:
            self.reason = "未配置 reranker 模型"
            return
        local_path = Path(self.model_name)
        is_local = local_path.is_dir()
        if not is_local and local_path.is_absolute():
            self.reason = f"reranker 模型路径不存在: {self.model_name}"
            return
        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            self.reason = "sentence-transformers 未安装（uv sync --extra local-embed）"
            return
        resolved = resolve_device(self.device)
        try:
            self._model = CrossEncoder(
                self.model_name,
                device=resolved,
                max_length=512,
                local_files_only=is_local,
            )
            self.resolved_device = resolved
        except Exception as exc:
            self.reason = f"reranker 加载失败（{Path(self.model_name).name}）: {type(exc).__name__}: {exc}"
            self._model = None

    def rerank(
        self, query: str, candidates: list[tuple[LawArticle, float]], top_k: int
    ) -> list[tuple[LawArticle, float]]:
        if self._model is None:
            raise RuntimeError("reranker 不可用")
        pairs = [(query, f"{article.law_name} {article.article_no} {article.text}") for article, _ in candidates]
        scores = self._model.predict(pairs, batch_size=8, show_progress_bar=False)
        ranked = sorted(
            zip((article for article, _ in candidates), (float(score) for score in scores)),
            key=lambda item: item[1],
            reverse=True,
        )
        return ranked[:top_k]
