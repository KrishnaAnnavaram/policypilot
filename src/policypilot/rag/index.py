"""Incremental hybrid index: BM25 + dense cosine, fused with reciprocal-rank fusion.

Documents are de-duplicated by content hash, so re-uploading a file is a no-op and new
uploads are added without rebuilding what is already indexed.
"""
from __future__ import annotations

import hashlib
import math
import threading
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from .embeddings import Embedder, HashingEmbedder
from .text import Chunk, chunk_text, detect_language, tokenize


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float
    rank: int


class Reranker(Protocol):
    def rerank(self, query: str, hits: list[Hit]) -> list[Hit]: ...


class HybridIndex:
    def __init__(self, embedder: Embedder | None = None, chunk_size: int = 700, overlap: int = 120,
                 k1: float = 1.5, b: float = 0.75, rrf_k: int = 60, reranker: Reranker | None = None):
        self.embedder = embedder or HashingEmbedder()
        self.chunk_size, self.overlap = chunk_size, overlap
        self.k1, self.b, self.rrf_k = k1, b, rrf_k
        self.reranker = reranker
        self.chunks: list[Chunk] = []
        self._vectors: list[list[float]] = []
        self._tf: list[Counter] = []
        self._df: Counter = Counter()
        self._total_len = 0
        self._hashes: set[str] = set()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self.chunks)

    @property
    def sources(self) -> list[str]:
        return sorted({c.source for c in self.chunks})

    def add_document(self, text: str, source: str, language: str | None = None) -> int:
        """Index one document; returns the number of new chunks (0 if already indexed or empty)."""
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        pieces = chunk_text(text, self.chunk_size, self.overlap) if text.strip() else []
        if not pieces:
            return 0
        vectors = self.embedder.embed(pieces, kind="passage")
        with self._lock:
            if digest in self._hashes:
                return 0
            self._hashes.add(digest)
            doc_id = digest[:12]
            for pos, (piece, vec) in enumerate(zip(pieces, vectors)):
                lang = language or detect_language(piece)
                self.chunks.append(Chunk(f"{doc_id}:{pos}", doc_id, source, piece, lang, pos))
                self._vectors.append(vec)
                tf = Counter(tokenize(piece))
                self._tf.append(tf)
                self._df.update(tf.keys())
                self._total_len += sum(tf.values())
        return len(pieces)

    def _bm25(self, query_tokens: list[str]) -> list[float]:
        n = len(self.chunks)
        avg_len = self._total_len / n if n else 0.0
        scores = []
        for tf in self._tf:
            length = sum(tf.values())
            s = 0.0
            for tok in set(query_tokens):
                f = tf.get(tok, 0)
                if not f:
                    continue
                df = self._df[tok]
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                s += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / (avg_len or 1)))
            scores.append(s)
        return scores

    def search(self, query: str, k: int = 4) -> list[Hit]:
        if not self.chunks or not query.strip():
            return []
        with self._lock:
            lexical = self._bm25(tokenize(query))
            qvec = self.embedder.embed([query], kind="query")[0]
            dense = [sum(a * b for a, b in zip(qvec, v)) for v in self._vectors]
            n = len(self.chunks)
            fused = [0.0] * n
            for scores in (lexical, dense):
                order = sorted(range(n), key=lambda i: scores[i], reverse=True)
                for rank, i in enumerate(order):
                    if scores[i] > 0:
                        fused[i] += 1.0 / (self.rrf_k + rank + 1)
            ranked = sorted((i for i in range(n) if fused[i] > 0), key=lambda i: fused[i], reverse=True)
            hits = [Hit(self.chunks[i], fused[i], r + 1) for r, i in enumerate(ranked[: max(k, 1) * 3])]
        if self.reranker is not None:
            hits = self.reranker.rerank(query, hits)
        return [Hit(h.chunk, h.score, r + 1) for r, h in enumerate(hits[:k])]


def merge_hits(results: list[list[Hit]], k: int) -> list[Hit]:
    """Merge hits from several indexes (e.g. shared documents + a user's uploads) by score."""
    seen: dict[str, Hit] = {}
    for hits in results:
        for h in hits:
            if h.chunk.chunk_id not in seen or seen[h.chunk.chunk_id].score < h.score:
                seen[h.chunk.chunk_id] = h
    ordered = sorted(seen.values(), key=lambda h: h.score, reverse=True)[:k]
    return [Hit(h.chunk, h.score, r + 1) for r, h in enumerate(ordered)]
