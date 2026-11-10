"""工具实现：只读本地语料与当前合同条款。

输出统一限长，避免把整部法律塞回模型上下文；``ok=false`` 表示业务上找不到
（例如条号不存在），由模型决定改引或拒引；参数/工具名错误则直接抛 ToolError，
在执行层归档成 ``tool.failed`` 事件。
"""

from __future__ import annotations

from typing import Any

from lexflow.models import Clause
from lexflow.retrieval.corpus import LawArticle
from lexflow.tools.definitions import (
    GetArticleArgs,
    GetClauseContextArgs,
    SearchStatutesArgs,
    ToolError,
    validate_args,
)

MAX_SNIPPET_CHARS = 300
MAX_ARTICLE_CHARS = 900
MAX_CLAUSE_CHARS = 800
MAX_RESULTS = 5


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…<truncated:{len(text)}>"


class CitationToolService:
    def __init__(
        self,
        articles: list[LawArticle],
        retriever: Any,
        clauses: dict[str, Clause] | list[Clause],
    ) -> None:
        self.articles = articles
        self.retriever = retriever
        if isinstance(clauses, dict):
            self.clauses = clauses
        else:
            self.clauses = {item.clause_id: item for item in clauses}
        self._by_key = {f"{item.law_name}#{item.article_no}": item for item in articles}

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        args = validate_args(name, arguments)
        if name == "search_statutes":
            return self._search(args)  # type: ignore[arg-type]
        if name == "get_article":
            return self._get_article(args)  # type: ignore[arg-type]
        if name == "get_clause_context":
            return self._get_clause(args)  # type: ignore[arg-type]
        raise ToolError("unknown_tool", f"未知工具: {name}")

    def _search(self, args: SearchStatutesArgs) -> dict[str, Any]:
        hits = self.retriever.retrieve(args.query, args.top_k, rerank=False)
        results = []
        for article, score in hits:
            if args.law_name and article.law_name != args.law_name:
                continue
            results.append(
                {
                    "law_name": article.law_name,
                    "article_no": article.article_no,
                    "chapter": article.chapter_path,
                    "snippet": _clip(article.text, MAX_SNIPPET_CHARS),
                    "score": round(float(score), 4),
                }
            )
            if len(results) >= MAX_RESULTS:
                break
        return {"ok": True, "query": args.query, "results": results, "count": len(results)}

    def _get_article(self, args: GetArticleArgs) -> dict[str, Any]:
        article = self._by_key.get(f"{args.law_name}#{args.article_no}")
        if article is None:
            # 不模糊匹配、不猜最近似条号：找不到就是找不到。
            return {
                "ok": False,
                "error": "article_not_found",
                "law_name": args.law_name,
                "article_no": args.article_no,
            }
        return {
            "ok": True,
            "law_name": article.law_name,
            "article_no": article.article_no,
            "chapter": article.chapter_path,
            "text": _clip(article.text, MAX_ARTICLE_CHARS),
            "effective_from": article.effective_from.isoformat() if article.effective_from else None,
            "effective_to": article.effective_to.isoformat() if article.effective_to else None,
        }

    def _get_clause(self, args: GetClauseContextArgs) -> dict[str, Any]:
        clause = self.clauses.get(args.clause_id)
        if clause is None:
            return {"ok": False, "error": "clause_not_found", "clause_id": args.clause_id}
        return {
            "ok": True,
            "clause_id": clause.clause_id,
            "text": _clip(clause.text, MAX_CLAUSE_CHARS),
        }
