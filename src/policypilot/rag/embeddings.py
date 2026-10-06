"""Embedders. The hashing embedder is dependency-free (offline demo, tests); the
sentence-transformers embedder is the recommended multilingual option."""
from __future__ import annotations

import math
import zlib
from typing import Protocol

from .text import tokenize


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str], kind: str = "passage") -> list[list[float]]: ...


class HashingEmbedder:
    """Signed feature hashing of word tokens, CJK n-grams and character trigrams.

    Deterministic (crc32), no model download. Good for lexical overlap within one
    language; it does NOT bridge languages - use a multilingual model for that.
    """

    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.name = f"hashing-{dim}"

    def _features(self, text: str) -> list[str]:
        feats = list(tokenize(text))
        for token in list(feats):
            if token.isascii() and len(token) > 4:
                padded = f"#{token}#"
                feats.extend(padded[i:i + 3] for i in range(len(padded) - 2))
        return feats

    def embed(self, texts: list[str], kind: str = "passage") -> list[list[float]]:
        vectors = []
        for text in texts:
            vec = [0.0] * self.dim
            for feat in self._features(text):
                h = zlib.crc32(feat.encode("utf-8"))
                vec[h % self.dim] += 1.0 if (h >> 16) & 1 else -1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vectors.append([v / norm for v in vec])
        return vectors


class SentenceTransformerEmbedder:  # pragma: no cover - needs the optional "rag" extra
    """Multilingual dense embeddings (default ``intfloat/multilingual-e5-small``).
    e5 models expect ``query:`` / ``passage:`` prefixes, added here."""

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        self.name = model_name
        self._model = SentenceTransformer(model_name)
        self._prefix = "e5" in model_name.lower()

    def embed(self, texts: list[str], kind: str = "passage") -> list[list[float]]:
        if self._prefix:
            texts = [f"{'query' if kind == 'query' else 'passage'}: {t}" for t in texts]
        return self._model.encode(texts, normalize_embeddings=True).tolist()


def build_embedder(kind: str, model_name: str) -> Embedder:
    if kind == "sentence-transformers":
        return SentenceTransformerEmbedder(model_name)
    if kind == "hashing":
        return HashingEmbedder()
    raise ValueError(f"unknown EMBEDDER {kind!r} (use 'hashing' or 'sentence-transformers')")
