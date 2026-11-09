"""本地 embedding（默认指向 E:\\project\\模型\\bge-m3）。

模型路径不存在、依赖未安装或加载失败时，设置明确 reason，由检索层降级 BM25-only。
"""

from __future__ import annotations

from pathlib import Path

from lexflow.devices import resolve_device


class LocalEmbedder:
    def __init__(
        self,
        model_name: str,
        *,
        device: str = "auto",
        enabled: bool = True,
        batch_size: int = 16,
    ) -> None:
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
        if self._model is not None:
            return
        if not self.enabled:
            self.reason = "embedding 已通过 LEXFLOW_EMBEDDING_ENABLED=false 禁用"
            return
        if not self.model_name:
            self.reason = "未配置 embedding 模型"
            return
        local_path = Path(self.model_name)
        is_local = local_path.is_dir()
        if not is_local and local_path.is_absolute():
            self.reason = f"embedding 模型路径不存在: {self.model_name}"
            return
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            self.reason = "sentence-transformers 未安装（uv sync --extra local-embed）"
            return

        resolved = resolve_device(self.device)
        try:
            self._model = SentenceTransformer(
                self.model_name,
                device=resolved,
                local_files_only=is_local,
            )
            self.resolved_device = resolved
        except Exception as exc:  # 模型损坏、显存不足、版本不兼容等都降级
            self.reason = f"embedding 模型加载失败（{Path(self.model_name).name}）: {type(exc).__name__}: {exc}"
            self._model = None

    def encode(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            raise RuntimeError("embedding 模型不可用，应使用 BM25-only 路径")
        vectors = self._model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [vector.tolist() for vector in vectors]
