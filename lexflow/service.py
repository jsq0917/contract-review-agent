"""业务服务层：CLI / FastAPI / Streamlit / eval 共用。"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from langgraph.types import Command

from lexflow.config import Settings, get_settings
from lexflow.failures import FailureLog
from lexflow.graph.build import ALL_CHANNELS, build_graph
from lexflow.llm.errors import classify_exception
from lexflow.llm.factory import build_provider
from lexflow.logging import get_logger, log_event, reset_trace_id, set_trace_id
from lexflow.retrieval.corpus import load_corpus
from lexflow.retrieval.hybrid import HybridRetriever
from lexflow.review.reviewer import PROMPT_VERSION
from lexflow.storage.db import Store
from lexflow.trace import Tracer

logger = get_logger("lexflow.service")


def _file_sha256(path: Path) -> str:
    """语料/模板的版本指纹；只读文件，不缓存，启动一次算一次。"""
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()[:12]
    except OSError:
        return "unknown"


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
    trace_id: str = ""


class AnalyzeService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.settings.ensure_dirs()
        self.store = Store(self.settings.db_path)
        self.failure_log = FailureLog(self.settings.failures_path)
        self.articles, self.corpus_mode = load_corpus(self.settings.corpus_path)
        self.corpus_version = _file_sha256(Path(self.settings.corpus_path))
        self.prompt_version = PROMPT_VERSION
        self.retriever = HybridRetriever(
            self.articles,
            self.settings.embedding_model,
            embedding_enabled=self.settings.embedding_enabled,
            embedding_device=self.settings.embedding_device,
            cache_dir=self.settings.embedding_cache_dir,
            reranker_model=self.settings.reranker_model,
            # 「能加载」与「默认使用」是两件事：能否加载看模型路径（load() 会给出原因），
            # 是否使用由每次请求的 rerank 参数决定。这里恒为 True 只是为了允许懒加载，
            # 不会在启动时载入权重，也不会让没开开关的请求走精排。
            reranker_enabled=True,
            reranker_device=self.settings.reranker_device,
            recover_retries=self.settings.embed_recover_retries,
            recover_cooldown_s=self.settings.embed_recover_cooldown_s,
        )
        # 精排预热的进度：/health 与 UI 都靠它区分“还在加载”“加载失败”“已就绪”。
        self._warmup_lock = threading.Lock()
        self._warmup: dict[str, Any] = {"status": "idle", "seconds": None, "reason": None}
        self._start_rerank_warmup()

    # ---------- 精排预热 ----------

    @property
    def warmup_state(self) -> dict[str, Any]:
        """status: idle / running / ready / failed / skipped。"""
        with self._warmup_lock:
            return dict(self._warmup)

    def _set_warmup(
        self, status: str, *, seconds: float | None = None, reason: str | None = None
    ) -> None:
        with self._warmup_lock:
            self._warmup = {"status": status, "seconds": seconds, "reason": reason}

    def _start_rerank_warmup(self) -> None:
        """后台加载精排模型，把 20s 的加载成本从首问挪到启动。

        默认只要模型在就预热：本机只配了一个 reranker，站在那儿不用它的概率很低，
        而首问白等 20 秒是实打实的体感问题。显存紧张时用 ``LEXFLOW_RERANKER_WARMUP=false``
        退回懒加载。预热是后台线程，不阻塞服务启动；加载失败只记原因，不影响检索。
        """
        if not self.settings.reranker_warmup:
            self._set_warmup("skipped", reason="LEXFLOW_RERANKER_WARMUP 已关闭")
            return
        if not self.retriever.reranker.model_present:
            self._set_warmup("skipped", reason="未配置 reranker 模型")
            return
        self._set_warmup("running")
        threading.Thread(
            target=self._warm_up_rerank, name="lexflow-rerank-warmup", daemon=True
        ).start()

    def _warm_up_rerank(self) -> None:
        started = time.perf_counter()
        reranker = self.retriever.reranker
        try:
            reranker.load()
        except Exception as exc:
            self._set_warmup(
                "failed",
                seconds=round(time.perf_counter() - started, 2),
                reason=f"{type(exc).__name__}: {exc}",
            )
            return
        elapsed = round(time.perf_counter() - started, 2)
        if not reranker.available:
            self._set_warmup("failed", seconds=elapsed, reason=reranker.reason or "未知原因")
            log_event(
                logger,
                "rerank.warmup_failed",
                model=reranker.model_name,
                latency=int(elapsed * 1000),
                status="failed",
                device=reranker.resolved_device or "n/a",
                error=reranker.reason,
            )
            return
        # 再跑一次真实前向：cuDNN/cuBLAS 的 kernel 选择在第一次 predict 时才发生，
        # 只加载不推理，“就绪”后的第一个请求仍要多等几百毫秒。
        try:
            sample = [(article, 0.0) for article in self.articles[:2]]
            reranker.rerank("预热", sample, 1)
        except Exception:
            pass
        self._set_warmup("ready", seconds=elapsed)
        log_event(
            logger,
            "rerank.warmup_ready",
            model=reranker.model_name,
            latency=int(elapsed * 1000),
            status="ok",
            device=reranker.resolved_device or "n/a",
        )

    def _graph(
        self,
        engine: str,
        *,
        matter_id: str,
        stance: str,
        as_of: str,
        channels: tuple[str, ...] | list[str] = ALL_CHANNELS,
    ) -> tuple[Any, Any, Tracer]:
        """建 provider + 图 + tracer。tracer 必须在建图前就绪，节点才能写同一个 trace。"""
        provider = build_provider(self.settings, engine)
        tracer = self._make_tracer(
            matter_id,
            provider_engine=provider.engine,
            model=provider.model,
            stance=stance,
            as_of=as_of,
            channels=channels,
        )
        bundle = build_graph(
            self.settings,
            self.store,
            self.retriever,
            provider,
            failure_log=self.failure_log,
            tracer=tracer,
            channels=channels,
        )
        return provider, bundle, tracer

    def _make_tracer(
        self,
        matter_id: str,
        *,
        provider_engine: str,
        model: str,
        stance: str,
        as_of: str,
        channels: tuple[str, ...] | list[str],
    ) -> Tracer:
        trace_id = f"trace-{matter_id}"
        path = self.settings.artifacts_dir / matter_id / "trace.jsonl"
        return Tracer(
            trace_id,
            path,
            meta={
                "matter_id": matter_id,
                "engine": provider_engine,
                "model": model,
                "prompt_version": self.prompt_version,
                "corpus_version": self.corpus_version,
                "stance": stance,
                "as_of": as_of,
                "channels": list(channels),
            },
        )

    def _rerank_metrics(self, requested: bool, before: dict[str, int]) -> dict[str, Any]:
        """按本次审查的增量算 rerank 指标。

        计数器挂在检索器实例上，并发 analyze 会互相混入——单机演示下精确，
        多用户共用同一进程时 ``rerank_used`` 可能把别人的精排算到本次头上。
        要彻底隔离得按请求建计数器；在那之前宁可把话说清楚，也不要谎报“已精排”。

        ``rerank_used`` 的口径是「这次检索有没有用上精排结果」：
        - ``applied > 0``：本次真的重新打分了；
        - ``applied == 0`` 且 ``cache_hits > 0``：命中同进程的精排缓存，结果仍是精排结果，
          同样算生效（缓存不是降级，也说明之前已经算过）；
        - 只有真正降级（模型不可用 / 打分失败，``fallback > 0``）才算未生效并给出原因。
        """
        after = self.retriever.rerank_stats
        applied = max(0, after["applied"] - before.get("applied", 0))
        fallback = max(0, after["fallback"] - before.get("fallback", 0))
        cache_hits = max(0, after["cache_hits"] - before.get("cache_hits", 0))
        used = applied > 0 or (cache_hits > 0 and fallback == 0)
        reason = None
        if requested and not used:
            reason = self.retriever.rerank_reason or "未发起精排调用"
        return {
            "rerank_requested": requested,
            "rerank_used": used,
            "rerank_applied": applied,
            "rerank_fallback": fallback,
            "rerank_cache_hits": cache_hits,
            "rerank_reason": reason,
        }

    def _finalize_report(
        self,
        matter_id: str,
        state: dict[str, Any],
        *,
        rerank_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """把完整节点耗时（含 assemble_report / export_docx）写回报告并持久化。

        assemble_report 节点生成 metrics 时只能看到它之前的 node_timings，
        export_docx 在中断恢复后才执行；因此在图返回后统一补齐。

        ``rerank_meta`` 只在 analyze 时传入：review 是续跑 export_docx，没有检索发生，
        传零值会把上一轮真实的精排指标覆盖成“没用过”。
        """
        report = dict(state.get("report") or {})
        timings = {key: int(value) for key, value in (state.get("node_timings") or {}).items()}
        if not report or not timings:
            return report
        metrics = dict(report.get("metrics") or {})
        metrics["node_latency_ms"] = timings
        metrics["node_latency_ms_total"] = sum(timings.values())
        metrics.setdefault("trace_id", f"trace-{matter_id}")
        if rerank_meta:
            metrics.update(rerank_meta)
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
        rerank: bool | None = None,
        channels: tuple[str, ...] | list[str] = ALL_CHANNELS,
        matter_id: str | None = None,
        token: str = "",
    ) -> RunResult:
        """``rerank=None`` 时取 ``LEXFLOW_RERANKER_ENABLED``；显式 True/False 覆盖它。

        ``channels`` 只用于单路/双路对照实验；默认两路并行，与旧行为一致。
        ``matter_id`` / ``token`` 由 API 任务化入口预先分配，CLI 与评测不传时自动生成。
        """
        started = time.perf_counter()
        rerank_on = self.settings.reranker_enabled if rerank is None else bool(rerank)
        stats_before = self.retriever.rerank_stats
        matter_id = matter_id or f"matter-{uuid.uuid4().hex[:10]}"
        source_dir = self.settings.artifacts_dir / matter_id
        source_dir.mkdir(parents=True, exist_ok=True)
        source = Path(file_path)
        copied = source_dir / source.name
        shutil.copy2(source, copied)

        provider, bundle, tracer = self._graph(
            engine,
            matter_id=matter_id,
            stance=stance,
            as_of=as_of or date.today().isoformat(),
            channels=channels,
        )
        trace_id = f"trace-{matter_id}"
        self.store.save_matter(
            matter_id,
            source.name,
            provider.engine,
            stance,
            as_of or date.today().isoformat(),
            token=token,
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
            "rerank": rerank_on,
            "engine": provider.engine,
            "trace_id": trace_id,
            "model_name": provider.model,
            "prompt_version": self.prompt_version,
            "corpus_version": self.corpus_version,
            "channels": list(channels),
            "findings": [],
            "evidence": [],
            "verifications": [],
            "warnings": [],
            "llm_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "node_timings": {},
            "llm_calls_meta": [],
            "tool_calls": [],
            "citation_resolutions": [],
            "retrieval_mode": self.retriever.mode,
            "retrieval_device": self.retriever.embedder.resolved_device or "n/a",
        }
        tracer.record(
            "request.start",
            filename=source.name,
            bytes=source.stat().st_size,
            engine=provider.engine,
            engine_requested=engine,
            require_confirmation=require_confirmation,
            rerank=rerank_on,
        )
        config = {"configurable": {"thread_id": matter_id}}
        token = set_trace_id(trace_id)
        try:
            state = bundle.graph.invoke(initial, config)
        except Exception as exc:
            self.store.update_matter(matter_id, status="failed")
            error = classify_exception(exc)
            elapsed = int((time.perf_counter() - started) * 1000)
            log_event(
                logger,
                "analyze.failed",
                matter_id=matter_id,
                node="graph",
                model=provider.model,
                tokens=0,
                latency=elapsed,
                status="error",
                device=self.retriever.embedder.resolved_device or "n/a",
                error_category=error.category,
                error_type=type(exc).__name__,
            )
            tracer.record(
                "request.failed",
                category=error.category,
                error_type=type(exc).__name__,
                latency_ms=elapsed,
            )
            raise
        finally:
            reset_trace_id(token)
            # 每次请求一条 checkpoint 连接；任务化后由后台线程持有，必须显式关掉。
            bundle.close()
        status = "awaiting_review" if "__interrupt__" in state else "completed"
        report = self._finalize_report(
            matter_id, state, rerank_meta=self._rerank_metrics(rerank_on, stats_before)
        )
        elapsed = int((time.perf_counter() - started) * 1000)
        tracer.record(
            "request.end",
            status=status,
            findings=len(report.get("findings", [])),
            llm_calls=int(report.get("metrics", {}).get("llm_calls", 0)),
            elapsed_ms=elapsed,
        )
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
            trace_id=trace_id,
        )

    @staticmethod
    def _restore_report_metrics(matter: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        """续跑时用已落库的报告补底，别把 analyze 阶段的指标抹掉。

        interrupt 点在 export_docx，state 里的 report 是 assemble_report 生成的版本；
        ``node_latency_ms``（含 export 的完整版）与 ``rerank_*`` 是图返回后才补进去的，
        只存在于库中。直接重新 finalize 会让这些字段消失——续跑不该改变已记录的结论。
        """
        raw = matter.get("result_json")
        if not raw:
            return state
        try:
            stored = json.loads(raw)
        except (TypeError, ValueError):
            return state
        stored_metrics = stored.get("metrics") or {}
        if not stored_metrics:
            return state
        current = state.get("report") or {}
        metrics = {**stored_metrics, **(current.get("metrics") or {})}
        report = dict(stored)
        report.update(current)
        report["metrics"] = metrics
        return {**state, "report": report}

    def review(self, matter_id: str, action: str = "approve", comment: str = "") -> RunResult:
        started = time.perf_counter()
        matter = self.store.get_matter(matter_id)
        if not matter:
            raise KeyError(f"matter 不存在: {matter_id}")
        if matter.get("status") == "cancelled":
            raise RuntimeError(f"matter 已取消，不能复核或导出: {matter_id}")
        if matter.get("status") == "completed" and matter.get("docx_path"):
            # 重复 review 不再跑图、不再调用模型：直接回放已落库结果，避免重复扣费与重复写盘。
            try:
                report = json.loads(matter.get("result_json") or "{}")
            except (TypeError, ValueError):
                report = {}
            return RunResult(
                matter_id=matter_id,
                status="completed",
                engine=str(matter.get("engine", "")),
                findings_count=len(report.get("findings", [])),
                docx_path=str(matter.get("docx_path") or ""),
                elapsed_ms=0,
                warnings=[],
                report=report,
                raw_state={},
                trace_id=f"trace-{matter_id}",
            )
        trace_id = f"trace-{matter_id}"
        provider, bundle, tracer = self._graph(
            matter["engine"],
            matter_id=matter_id,
            stance=str(matter.get("stance", "")),
            as_of=str(matter.get("as_of", "")),
            channels=ALL_CHANNELS,
        )
        tracer.record("request.resume", action=action, comment_chars=len(comment))
        config = {"configurable": {"thread_id": matter_id}}
        token = set_trace_id(trace_id)
        try:
            state = bundle.graph.invoke(Command(resume={"action": action, "comment": comment}), config)
        finally:
            reset_trace_id(token)
            bundle.close()
        state = self._restore_report_metrics(matter, state)
        report = self._finalize_report(matter_id, state)
        matter = self.store.get_matter(matter_id) or matter
        elapsed = int((time.perf_counter() - started) * 1000)
        tracer.record(
            "request.end",
            status="completed" if state.get("docx_path") else matter["status"],
            action=action,
            elapsed_ms=elapsed,
        )
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
            trace_id=trace_id,
        )

    def get_docx_path(self, matter_id: str) -> Path:
        matter = self.store.get_matter(matter_id)
        if not matter:
            raise KeyError(f"matter 不存在: {matter_id}")
        path = matter.get("docx_path")
        if not path or not Path(path).exists():
            raise FileNotFoundError("docx 尚未生成，请先完成复核")
        return Path(path)
