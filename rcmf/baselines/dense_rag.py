from __future__ import annotations

from rcmf.baselines.bm25 import BM25RAGPolicy


class DenseRAGPolicy(BM25RAGPolicy):
    """Controlled placeholder until a fixed embedding model is configured.

    It intentionally behaves like BM25 rather than claiming dense retrieval
    numbers. Use only after wiring Qwen3-Embedding-4B or another fixed encoder.
    """

