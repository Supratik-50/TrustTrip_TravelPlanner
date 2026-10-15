"""Embedding Providers for TrustTrip Phase 8 RAG.

Provides:
- BGESmallEmbeddingProvider: Production semantic embedding provider using BAAI/bge-small-en-v1.5
  with 384 dimensions, normalized finite vectors, query instruction prefix, and GPU/CPU auto-detection.
- DeterministicEmbeddingProvider: Fast offline deterministic hashing provider for unit tests.
- get_embedding_provider(): Factory enforcing fail-closed production selection.
- cosine_similarity(): Unit vector dot product calculation.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import threading
from typing import Any

from services.research.ports import EmbeddingProviderPort

logger = logging.getLogger(__name__)


class BGESmallEmbeddingProvider(EmbeddingProviderPort):
    """Production semantic embedding provider using BAAI/bge-small-en-v1.5 via sentence-transformers.

    Outputs 384-dimensional unit-normalized dense vectors.
    Uses model card recommended query instruction prefix for search queries:
    "Represent this sentence for searching relevant passages: "
    Passages are encoded without any query prefix.
    """

    MODEL_NAME = "BAAI/bge-small-en-v1.5"
    DIMENSION = 384
    QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

    _MODELS: dict[str, Any] = {}
    _LOCK = threading.Lock()
    _INIT_COUNT: int = 0

    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
        model_id: str | None = None,
    ) -> None:
        self.model_name = model_id or model_name or os.getenv("EMBEDDING_MODEL", self.MODEL_NAME)
        self.device = device
        self.dimension = self.DIMENSION

    @classmethod
    def _get_model(cls, model_name: str, device: str | None = None) -> Any:
        import os
        import torch

        pid = os.getpid()
        target_device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        cache_key = f"{model_name}:{target_device}"

        if cache_key in cls._MODELS:
            logger.info(
                "[EMBEDDING_LIFECYCLE] PID=%d CACHE_HIT=True provider=%s model='%s' init_count=%d",
                pid,
                cls.__name__,
                model_name,
                cls._INIT_COUNT,
            )
            return cls._MODELS[cache_key]

        with cls._LOCK:
            if cache_key in cls._MODELS:
                logger.info(
                    "[EMBEDDING_LIFECYCLE] PID=%d CACHE_HIT=True (post-lock) provider=%s model='%s' init_count=%d",
                    pid,
                    cls.__name__,
                    model_name,
                    cls._INIT_COUNT,
                )
                return cls._MODELS[cache_key]

            try:
                from sentence_transformers import SentenceTransformer

                cls._INIT_COUNT += 1
                logger.info(
                    "[EMBEDDING_LIFECYCLE] PID=%d CACHE_MISS=True Loading semantic embedding model '%s' on %s (init_count=%d)...",
                    pid,
                    model_name,
                    target_device,
                    cls._INIT_COUNT,
                )
                model = SentenceTransformer(model_name, device=target_device)
                cls._MODELS[cache_key] = model
                logger.info(
                    "[EMBEDDING_LIFECYCLE] PID=%d Successfully loaded semantic embedding model '%s' (init_count=%d).",
                    pid,
                    model_name,
                    cls._INIT_COUNT,
                )
            except Exception as exc:
                cls._MODELS.pop(cache_key, None)
                raise RuntimeError(
                    f"Failed to load BGE embedding model '{model_name}': {exc}. "
                    "Production semantic RAG requires BGE embedding model to load successfully. "
                    "Silent fallback to hashing embeddings in production is strictly prohibited."
                ) from exc
        return cls._MODELS[cache_key]

    def _validate_vector(self, vec: list[float]) -> list[float]:
        if len(vec) != self.dimension:
            raise ValueError(f"BGE vector dimension mismatch: expected {self.dimension}, got {len(vec)}")
        for val in vec:
            if not math.isfinite(val):
                raise ValueError("BGE vector contains non-finite values (NaN or Inf)")
        return vec

    def embed_texts(self, texts: list[str], is_query: bool = False) -> list[list[float]]:
        """Generate unit-normalized 384-dimensional dense vectors for a list of texts."""
        if not texts:
            return []

        model = self._get_model(self.model_name, self.device)

        processed_texts: list[str] = []
        for t in texts:
            clean_t = t.strip() if t else ""
            if is_query and clean_t:
                processed_texts.append(self.QUERY_INSTRUCTION + clean_t)
            else:
                processed_texts.append(clean_t if clean_t else " ")

        raw_embeddings = model.encode(
            processed_texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        vectors: list[list[float]] = []
        for row in raw_embeddings:
            vec = [float(x) for x in row]
            vectors.append(self._validate_vector(vec))

        return vectors

    def embed_query(self, query: str) -> list[float]:
        """Generate unit-normalized 384-dimensional dense vector for a retrieval query."""
        results = self.embed_texts([query], is_query=True)
        return results[0] if results else [0.0] * self.dimension


class DeterministicEmbeddingProvider(EmbeddingProviderPort):
    """Deterministic hashing-based dense text embedding generator for fast offline tests."""

    def __init__(self, dimension: int = 384, dim: int | None = None) -> None:
        self.dimension = dim if dim is not None else dimension

    def _embed_single(self, text: str) -> list[float]:
        """Convert a text string into an L2-normalized dense vector."""
        vec = [0.0] * self.dimension
        if not text:
            return vec

        tokens = re.findall(r"\w+", text.lower())
        if not tokens:
            return vec

        for word in tokens:
            h_word = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
            idx = h_word % self.dimension
            sign = 1.0 if (h_word // self.dimension) % 2 == 0 else -1.0
            vec[idx] += 1.0 * sign

            if len(word) >= 3:
                for i in range(len(word) - 2):
                    ngram = word[i : i + 3]
                    h_ng = int(hashlib.md5(ngram.encode("utf-8")).hexdigest(), 16)
                    idx_ng = h_ng % self.dimension
                    sign_ng = 1.0 if (h_ng // self.dimension) % 2 == 0 else -1.0
                    vec[idx_ng] += 0.5 * sign_ng

        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 1e-9:
            return [x / norm for x in vec]
        return vec

    def embed_texts(self, texts: list[str], is_query: bool = False) -> list[list[float]]:
        """Generate deterministic embeddings for a list of texts."""
        return [self._embed_single(t) for t in texts]

    def embed_query(self, query: str) -> list[float]:
        """Generate deterministic embedding for a search query."""
        return self._embed_single(query)


_GLOBAL_PROVIDER: EmbeddingProviderPort | None = None
_PROVIDER_LOCK = threading.Lock()


def get_embedding_provider() -> EmbeddingProviderPort:
    """Return process-level singleton embedding provider.

    MULTI-WORKER PROCESS ISOLATION NOTE:
    Each OS worker process (e.g. in multi-worker Uvicorn or Gunicorn deployments)
    has its own separate memory address space. Each worker initializes and reuses
    exactly ONE in-memory model instance on demand when its first embedding-dependent
    request arrives. Subsequent queries and document ingestion in that worker reuse
    the same in-memory instance without re-loading weights.
    Web Research queries do NOT invoke this provider when no document RAG is requested.

    Thread-safe lazy initialization ensures concurrent requests within the process
    do not trigger duplicate model initializations.
    """
    global _GLOBAL_PROVIDER

    provider = os.getenv("EMBEDDING_PROVIDER", "").strip().lower()
    env = os.getenv("ENVIRONMENT", "").strip().lower()
    testing = os.getenv("TESTING", "").strip()
    is_det_requested = provider in ("deterministic", "mock", "hash") or env == "test" or testing == "1"

    if _GLOBAL_PROVIDER is not None:
        if is_det_requested and isinstance(_GLOBAL_PROVIDER, DeterministicEmbeddingProvider):
            return _GLOBAL_PROVIDER
        if not is_det_requested and isinstance(_GLOBAL_PROVIDER, BGESmallEmbeddingProvider):
            return _GLOBAL_PROVIDER

    with _PROVIDER_LOCK:
        if _GLOBAL_PROVIDER is not None:
            if is_det_requested and isinstance(_GLOBAL_PROVIDER, DeterministicEmbeddingProvider):
                return _GLOBAL_PROVIDER
            if not is_det_requested and isinstance(_GLOBAL_PROVIDER, BGESmallEmbeddingProvider):
                return _GLOBAL_PROVIDER

        if is_det_requested:
            _GLOBAL_PROVIDER = DeterministicEmbeddingProvider(dimension=384)
            return _GLOBAL_PROVIDER

        if provider in ("bge", ""):
            _GLOBAL_PROVIDER = BGESmallEmbeddingProvider()
            return _GLOBAL_PROVIDER

        raise RuntimeError(
            f"Unknown or unsupported EMBEDDING_PROVIDER: '{provider}'. "
            "Valid options are 'bge' (production) or 'deterministic' (tests)."
        )


def reset_embedding_provider_for_testing() -> None:
    """Reset the singleton embedding provider and cached models for hermetic unit testing."""
    global _GLOBAL_PROVIDER
    with _PROVIDER_LOCK:
        _GLOBAL_PROVIDER = None
    with BGESmallEmbeddingProvider._LOCK:
        BGESmallEmbeddingProvider._MODELS.clear()
        BGESmallEmbeddingProvider._INIT_COUNT = 0


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two unit-normalized vectors."""
    if len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    return max(-1.0, min(1.0, dot))
