"""Embedding Provider for TrustTrip Phase 8 RAG.

Provides deterministic, unit-normalized dense embeddings without heavy C/PyTorch dependencies.
Supports cosine similarity via dot product.
"""
from __future__ import annotations

import hashlib
import math
import re
from services.research.ports import EmbeddingProviderPort


class DeterministicEmbeddingProvider(EmbeddingProviderPort):
    """Deterministic hashing-based dense text embedding generator."""

    def __init__(self, dimension: int = 128, dim: int | None = None) -> None:
        self.dimension = dim if dim is not None else dimension

    def _embed_single(self, text: str) -> list[float]:
        """Convert a text string into an L2-normalized dense vector."""
        vec = [0.0] * self.dimension
        if not text:
            return vec

        # Tokenize and extract character n-grams + words
        tokens = re.findall(r"\w+", text.lower())
        if not tokens:
            return vec

        # Build feature counts
        for word in tokens:
            # Word feature
            h_word = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
            idx = h_word % self.dimension
            sign = 1.0 if (h_word // self.dimension) % 2 == 0 else -1.0
            vec[idx] += 1.0 * sign

            # Character 3-grams for morphological robustness
            if len(word) >= 3:
                for i in range(len(word) - 2):
                    ngram = word[i : i + 3]
                    h_ng = int(hashlib.md5(ngram.encode("utf-8")).hexdigest(), 16)
                    idx_ng = h_ng % self.dimension
                    sign_ng = 1.0 if (h_ng // self.dimension) % 2 == 0 else -1.0
                    vec[idx_ng] += 0.5 * sign_ng

        # Compute Euclidean norm
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 1e-9:
            return [x / norm for x in vec]
        return vec

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts."""
        return [self._embed_single(t) for t in texts]


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two unit-normalized vectors."""
    if len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    # Bound to [-1.0, 1.0]
    return max(-1.0, min(1.0, dot))
