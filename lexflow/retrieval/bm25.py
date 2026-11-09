"""轻量 BM25 实现，避免额外依赖；中文采用字符二元组。"""

from __future__ import annotations

import math
import re
from collections import Counter

CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
ALNUM_RE = re.compile(r"[a-zA-Z0-9]+")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for chunk in CJK_RE.findall(text):
        if len(chunk) == 1:
            tokens.append(chunk)
        else:
            tokens.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
    tokens.extend(word.lower() for word in ALNUM_RE.findall(text))
    return tokens


class BM25:
    def __init__(self, documents: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.tokens = [tokenize(doc) for doc in documents]
        self.doc_len = [len(tokens) for tokens in self.tokens]
        self.avg_len = sum(self.doc_len) / max(len(self.doc_len), 1)
        self.doc_freq: Counter[str] = Counter()
        for tokens in self.tokens:
            self.doc_freq.update(set(tokens))
        self.n = len(documents)
        self.term_freq = [Counter(tokens) for tokens in self.tokens]

    def scores(self, query: str) -> list[float]:
        query_tokens = tokenize(query)
        result = [0.0] * self.n
        for term in set(query_tokens):
            df = self.doc_freq.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            for index, freq in enumerate(self.term_freq):
                tf = freq.get(term, 0)
                if tf == 0:
                    continue
                denom = tf + self.k1 * (
                    1 - self.b + self.b * self.doc_len[index] / max(self.avg_len, 1e-9)
                )
                result[index] += idf * (tf * (self.k1 + 1) / denom)
        return result

    def search(self, query: str, top_k: int = 5) -> list[tuple[int, float]]:
        scores = self.scores(query)
        ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)
        return [(index, score) for index, score in ranked[:top_k] if score > 0]
