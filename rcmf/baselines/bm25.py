from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from rcmf.model.backends.base import ChatMessage, ModelBackend
from rcmf.schemas import MemoryRecord


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]


class BM25Index:
    def __init__(self, records: list[MemoryRecord], k1: float = 1.5, b: float = 0.75) -> None:
        self.records = records
        self.k1 = k1
        self.b = b
        self.doc_tokens = [tokenize(record.experience_text) for record in records]
        self.doc_lengths = [len(tokens) for tokens in self.doc_tokens]
        self.avgdl = sum(self.doc_lengths) / max(1, len(self.doc_lengths))
        self.term_freqs = [Counter(tokens) for tokens in self.doc_tokens]
        self.doc_freq: dict[str, int] = defaultdict(int)
        for tokens in self.doc_tokens:
            for token in set(tokens):
                self.doc_freq[token] += 1

    def score(self, query: str, index: int) -> float:
        query_terms = tokenize(query)
        if not query_terms:
            return 0.0
        score = 0.0
        n_docs = max(1, len(self.records))
        doc_len = self.doc_lengths[index]
        tf = self.term_freqs[index]
        for term in query_terms:
            df = self.doc_freq.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            freq = tf.get(term, 0)
            denom = freq + self.k1 * (1 - self.b + self.b * doc_len / max(self.avgdl, 1e-6))
            score += idf * (freq * (self.k1 + 1)) / max(denom, 1e-6)
        return score

    def search(self, query: str, top_k: int = 4) -> list[MemoryRecord]:
        scored = [(self.score(query, index), record) for index, record in enumerate(self.records)]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [record for score, record in scored[:top_k] if score > 0]


class BM25RAGPolicy:
    def __init__(self, backend: ModelBackend, records: list[MemoryRecord], top_k: int = 4) -> None:
        self.backend = backend
        self.index = BM25Index(records)
        self.top_k = top_k

    def generate(self, messages: list[ChatMessage], **kwargs):
        query = "\n".join(msg["content"] for msg in messages if msg["role"] == "user")
        memories = self.index.search(query, top_k=self.top_k)
        augmented = [dict(msg) for msg in messages]
        if memories:
            context = "\n\n".join(record.experience_text for record in memories)
            augmented.insert(1, {"role": "system", "content": f"[RETRIEVED MEMORY]\n{context}"})
        return self.backend.generate(messages=augmented, injector=None, memory_z=None, **kwargs)

