"""LangGraph 1.x 单主图：

parse → segment_clauses → (review_legal ‖ review_commercial) → bind_evidence
→ verify → assemble_report → export_docx
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from functools import partial

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from lexflow.config import Settings
from lexflow.failures import FailureLog
from lexflow.graph.nodes import (
    assemble_report_node,
    bind_evidence_node,
    export_docx_node,
    parse_node,
    segment_node,
    verify_node,
    _review_node,
)
from lexflow.graph.state import ReviewState
from lexflow.llm.base import LLMProvider
from lexflow.retrieval.hybrid import HybridRetriever
from lexflow.storage.db import Store


@dataclass
class GraphBundle:
    graph: object
    saver: SqliteSaver
    checkpoint_conn: sqlite3.Connection


def build_graph(
    settings: Settings,
    store: Store,
    retriever: HybridRetriever,
    provider: LLMProvider,
    *,
    failure_log: FailureLog | None = None,
) -> GraphBundle:
    failure_log = failure_log or FailureLog(settings.failures_path)
    builder = StateGraph(ReviewState)
    builder.add_node("parse", parse_node)
    builder.add_node("segment_clauses", partial(segment_node, store=store))
    builder.add_node(
        "review_legal",
        partial(
            _review_node,
            reviewer="legal",
            provider=provider,
            retriever=retriever,
            failure_log=failure_log,
            top_k=settings.statute_top_k,
        ),
    )
    builder.add_node(
        "review_commercial",
        partial(
            _review_node,
            reviewer="commercial",
            provider=provider,
            retriever=retriever,
            failure_log=failure_log,
            top_k=settings.statute_top_k,
        ),
    )
    builder.add_node("bind_evidence", partial(bind_evidence_node, retriever=retriever))
    builder.add_node("verify", verify_node)
    builder.add_node("assemble_report", partial(assemble_report_node, store=store, settings=settings))
    builder.add_node(
        "export_docx",
        partial(
            export_docx_node,
            store=store,
            template_path=str(settings.template_path),
            artifacts_dir=str(settings.artifacts_dir),
        ),
    )

    builder.add_edge(START, "parse")
    builder.add_edge("parse", "segment_clauses")
    builder.add_edge("segment_clauses", "review_legal")
    builder.add_edge("segment_clauses", "review_commercial")
    builder.add_edge("review_legal", "bind_evidence")
    builder.add_edge("review_commercial", "bind_evidence")
    builder.add_edge("bind_evidence", "verify")
    builder.add_edge("verify", "assemble_report")
    builder.add_edge("assemble_report", "export_docx")
    builder.add_edge("export_docx", END)

    checkpoint_conn = sqlite3.connect(str(settings.db_path), check_same_thread=False)
    checkpoint_conn.execute("PRAGMA journal_mode=WAL;")
    # 显式登记项目 Pydantic 类型，避免 LangGraph 未来版本拒绝反序列化。
    serde = JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("lexflow.models", "Finding"),
            ("lexflow.models", "Evidence"),
            ("lexflow.models", "Verification"),
        ]
    )
    saver = SqliteSaver(checkpoint_conn, serde=serde)
    graph = builder.compile(checkpointer=saver)
    return GraphBundle(graph=graph, saver=saver, checkpoint_conn=checkpoint_conn)
