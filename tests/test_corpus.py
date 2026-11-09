import hashlib
import json
from pathlib import Path

from lexflow.config import ROOT
from lexflow.corpus_build import build_corpus
from lexflow.retrieval.corpus import load_corpus
from lexflow.retrieval.hybrid import HybridRetriever

CORPUS_DIR = ROOT / "data" / "corpus"
CORPUS_FILE = CORPUS_DIR / "law_articles.jsonl"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_full_corpus_counts_and_uniqueness():
    articles, mode = load_corpus(CORPUS_FILE)
    assert mode == "external"
    assert len(articles) == 1465
    by_law = {"cc": 0, "ll": 0, "lcl": 0}
    for article in articles:
        by_law[article.law_id] += 1
    assert by_law == {"cc": 1260, "ll": 107, "lcl": 98}
    keys = {(article.law_id, article.article_no) for article in articles}
    assert len(keys) == len(articles)


def test_content_hash_matches_text():
    articles, _mode = load_corpus(CORPUS_FILE)
    for article in articles:
        expected = hashlib.sha256(article.text.encode("utf-8")).hexdigest()
        assert article.content_hash == expected
        assert "⺠" not in article.text and "⻔" not in article.text


def test_key_articles_and_boundaries():
    articles, _mode = load_corpus(CORPUS_FILE)
    key = {(article.law_id, article.article_no): article for article in articles}
    assert key[("cc", "第五百八十五条")].effective_from.isoformat() == "2021-01-01"
    assert key[("ll", "第一百零七条")].text.startswith("本法自1995年1月1日起施行")
    assert key[("lcl", "第九十八条")].text.startswith("本法自 2008 年 1 月 1 日起施行")
    assert "新华社" not in key[("cc", "第一千二百六十条")].text
    assert "八次会议通过）" not in key[("lcl", "第九十八条")].text
    assert key[("lcl", "第十九条")].text.startswith("劳动合同期限三个月以上不满一年的")


def test_kangxi_normalization_log_is_complete():
    log_path = CORPUS_DIR / "labor_law_normalization.jsonl"
    records = [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(records) == 107
    assert sum(record["chars_replaced"] for record in records) == 1018
    for record in records:
        assert record["text_before"] != record["text_after"]
        assert any(0x2E80 <= ord(char) <= 0x2FDF for char in record["text_before"])
        assert not any(0x2E80 <= ord(char) <= 0x2FDF for char in record["text_after"])

    manifest = json.loads((CORPUS_DIR / "corpus_manifest.json").read_text(encoding="utf-8"))
    assert manifest["normalization"]["source_chars"] == 1122
    assert manifest["normalization"]["article_chars"] == 1018
    assert manifest["normalization"]["affected_articles"] == 107
    assert manifest["files"]["law_articles.jsonl"] == _sha256(CORPUS_FILE)
    assert manifest["files"]["labor_law_normalization.jsonl"] == _sha256(log_path)


def test_build_is_idempotent(tmp_path: Path):
    first = build_corpus(tmp_path / "run1")
    first_hash = first["files"]["law_articles.jsonl"]
    second = build_corpus(tmp_path / "run2")
    second_hash = second["files"]["law_articles.jsonl"]
    assert first_hash == second_hash
    assert first["files"]["labor_law_normalization.jsonl"] == second["files"][
        "labor_law_normalization.jsonl"
    ]
    assert first_hash == _sha256(CORPUS_FILE)


def _small_articles(limit: int = 20):
    articles, _mode = load_corpus(CORPUS_FILE)
    return articles[:limit]


def test_retriever_falls_back_to_bm25_when_model_path_missing():
    articles = _small_articles()
    retriever = HybridRetriever(
        articles, str(ROOT / "no-such-model"), embedding_enabled=True, cache_dir=None
    )
    assert retriever.mode == "bm25-only"
    assert "路径不存在" in retriever.vector_reason
    hits = retriever.search("民事主体", top_k=3)
    assert hits and hits[0][0].law_id == "cc"
    assert retriever.search("民事主体", top_k=3, mode="dense") == []


def test_retriever_falls_back_when_embedding_disabled():
    retriever = HybridRetriever(
        _small_articles(), "disabled", embedding_enabled=False, cache_dir=None
    )
    assert retriever.mode == "bm25-only"
    assert "禁用" in retriever.vector_reason
    assert retriever.search("民事主体", top_k=2)


def test_reranker_missing_does_not_break_hybrid():
    retriever = HybridRetriever(
        _small_articles(),
        "disabled",
        embedding_enabled=False,
        cache_dir=None,
        reranker_model=str(ROOT / "no-such-reranker"),
        reranker_enabled=True,
    )
    hits = retriever.search_reranked("民事主体", top_k=2)
    assert hits
    assert "路径不存在" in retriever.rerank_reason
