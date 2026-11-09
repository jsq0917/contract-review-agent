"""业务服务层：CLI / FastAPI / Streamlit / eval 共用。"""

from __future__ import annotations

import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from langgraph.types import Command

from lexflow.config import Settings, get_settings
from lexflow.failures import FailureLog
from lexflow.graph.build import build_graph
from lexflow.llm.factory import build_provider
from lexflow.logging import get_logger, log_event
from lexflow.retrieval.corpus import load_corpus
from lexflow.retrieval.hybrid import HybridRetriever
from lexflow.storage.db import Store

logger = get_logger("lexflow.service")


@dataclass
class RunResult:
    matter_id: str
    status: str
    engine: str
    findings_count: int
    docx_path: str | None
    elapsed_ms: int
    warnings: list[str] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)
    raw_state: dict[str, Any] = field(default_factory=dict)


class AnalyzeService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.settings.ensure_dirs()
        self.store = Store(self.settings.db_path)
        self.failure_log = FailureLog(self.settings.failures_path)
        self.articles, self.corpus_mode = load_corpus(self.settings.corpus_path)
        self.retriever = HybridRetriever(
            self.articles,
            self.settings.embedding_model,
            embedding_enabled=self.settings.embedding_enabled,
            embedding_device=self.settings.embedding_device,
            cache_dir=self.settings.embedding_cache_dir,
            reranker_model=self.settings.reranker_model,
            reranker_enabled=self.settings.reranker_enabled,
            reranker_device=self.settings.reranker_device,
        )

    def _graph(self, engine: str):
        provider = build_provider(self.settings, engine)
        bundle = build_graph(
            self.settings, self.store, self.retriever, provider, failure_log=self.failure_log
        )
        return provider, bundle

    def _finalize_report(self, matter_id: str, state: dict[str, Any]) -> dict[str, Any]:
        """把完整节点耗时（含 assemble_report / export_docx）写回报告并持久化。

        assemble_report 节点生成 metrics 时只能看到它之前的 node_timings，
        export_docx 在中断恢复后才执行；因此在图返回后统一补齐。
        """
        report = dict(state.get("report") or {})
        timings = {key: int(value) for key, value in (state.get("node_timings") or {}).items()}
        if not report or not timings:
            return report
        metrics = dict(report.get("metrics") or {})
        metrics["node_latency_ms"] = timings
        metrics["node_latency_ms_total"] = sum(timings.values())
        report["metrics"] = metrics
        self.store.update_matter(matter_id, result=report)
        return report

    def analyze(
        self,
        file_path: str | Path,
        *,
        stance: str = "甲方",
        as_of: str | None = None,
        engine: str = "auto",
        require_confirmation: bool = True,
        auto_approve: bool = False,
    ) -> RunResult:
        started = time.perf_counter()
        matter_id = f"matter-{uuid.uuid4().hex[:10]}"
        source_dir = self.settings.artifacts_dir / matter_id
        source_dir.mkdir(parents=True, exist_ok=True)
        source = Path(file_path)
        copied = source_dir / source.name
        shutil.copy2(source, copied)

        provider, bundle = self._graph(engine)
        self.store.save_matter(
            matter_id, source.name, provider.engine, stance, as_of or date.today().isoformat()
        )
        initial = {
            "matter_id": matter_id,
            "doc_path": str(copied),
            "filename": source.name,
            "stance": stance,
            "as_of": as_of or date.today().isoformat(),
            "engine_requested": engine,
            "require_confirmation": require_confirmation,
            "auto_approve": auto_approve,
            "engine": provider.engine,
            "findings": [],
            "evidence": [],
            "verifications": [],
            "warnings": [],
            "llm_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "node_timings": {},
            "retrieval_mode": self.retriever.mode,
            "retrieval_device": self.retriever.embedder.resolved_device or "n/a",
        }
        config = {"configurable": {"thread_id": matter_id}}
        try:
            state = bundle.graph.invoke(initial, config)
        except Exception as exc:
            self.store.update_matter(matter_id, status="failed")
            log_event(
                logger,
                "analyze.failed",
                matter_id=matter_id,
                node="graph",
                model=provider.model,
                tokens=0,
                latency=int((time.perf_counter() - started) * 1000),
                status="error",
                device=self.retriever.embedder.resolved_device or "n/a",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        status = "awaiting_review" if "__interrupt__" in state else "completed"
        report = self._finalize_report(matter_id, state)
        elapsed = int((time.perf_counter() - started) * 1000)
        return RunResult(
            matter_id=matter_id,
            status=status,
            engine=provider.engine,
            findings_count=len(report.get("findings", [])),
            docx_path=state.get("docx_path"),
            elapsed_ms=elapsed,
            warnings=list(state.get("warnings", [])),
            report=report,
            raw_state={k: v for k, v in state.items() if k != "__interrupt__"},
        )

    def review(self, matter_id: str, action: str = "approve", comment: str = "") -> RunResult:
        started = time.perf_counter()
        matter = self.store.get_matter(matter_id)
        if not matter:
            raise KeyError(f"matter 不存在: {matter_id}")
        provider, bundle = self._graph(matter["engine"])
        config = {"configurable": {"thread_id": matter_id}}
        state = bundle.graph.invoke(Command(resume={"action": action, "comment": comment}), config)
        report = self._finalize_report(matter_id, state)
        matter = self.store.get_matter(matter_id) or matter
        elapsed = int((time.perf_counter() - started) * 1000)
        return RunResult(
            matter_id=matter_id,
            status="completed" if state.get("docx_path") else matter["status"],
            engine=provider.engine,
            findings_count=len(report.get("findings", [])),
            docx_path=state.get("docx_path"),
            elapsed_ms=elapsed,
            warnings=list(state.get("warnings", [])),
            report=report,
            raw_state={k: v for k, v in state.items() if k != "__interrupt__"},
        )

    def get_docx_path(self, matter_id: str) -> Path:
        matter = self.store.get_matter(matter_id)
        if not matter:
            raise KeyError(f"matter 不存在: {matter_id}")
        path = matter.get("docx_path")
        if not path or not Path(path).exists():
            raise FileNotFoundError("docx 尚未生成，请先完成复核")
        return Path(path)
