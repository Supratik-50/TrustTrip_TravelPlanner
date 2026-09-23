"""Provider Abstractions and Ports for TrustTrip Phase 8 Research.

Defines protocol interfaces for:
- Web Search Providers
- Safe HTTP Page Fetchers
- Embedding Models
- Vector Stores
- Rerankers
- LLM Synthesizers
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from uuid import UUID
from domain.research import ResearchSource, RagChunk


class ResearchProviderError(Exception):
    """Base error for research providers."""
    pass


class ResearchRateLimitError(ResearchProviderError):
    """Raised when research provider exceeds rate limits (HTTP 429)."""
    pass


class ResearchTimeoutError(ResearchProviderError):
    """Raised when research provider request times out."""
    pass


class LLMProviderUnavailableError(ResearchProviderError):
    """Raised when LLM provider is unavailable, times out, or fails."""
    pass


class SSRFSecurityViolation(Exception):
    """Raised when an outbound URL fails SSRF security checks."""
    pass


@runtime_checkable
class ResearchProviderPort(Protocol):
    """Port for web search research providers."""

    async def search(
        self,
        query: str,
        max_results: int = 5,
        call_timeout: float = 10.0,
    ) -> list[ResearchSource]:
        """Execute a web search query returning normalized research sources."""
        ...


@runtime_checkable
class FetcherPort(Protocol):
    """Port for fetching web page content securely."""

    async def fetch(
        self,
        url: str,
        call_timeout: float = 8.0,
    ) -> tuple[str, str, int]:
        """Fetch raw HTML/text, returning (content, final_url, status_code)."""
        ...


@runtime_checkable
class EmbeddingProviderPort(Protocol):
    """Port for text embeddings."""

    def embed_texts(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """Generate unit-normalized embedding vectors for a list of texts."""
        ...


@runtime_checkable
class VectorStorePort(Protocol):
    """Port for vector storage and similarity search."""

    def upsert_chunks(
        self,
        chunks: list[RagChunk],
        embeddings: list[list[float]],
    ) -> int:
        """Store document chunks and their corresponding embedding vectors."""
        ...

    def query_similar(
        self,
        query_embedding: list[float],
        user_id: UUID,
        top_k: int = 8,
        filter_document_ids: list[UUID] | None = None,
    ) -> list[tuple[RagChunk, float]]:
        """Retrieve top_k chunks for user_id with cosine similarity score."""
        ...

    def delete_document(
        self,
        document_id: UUID,
        user_id: UUID,
    ) -> int:
        """Delete all chunks and vectors belonging to a document."""
        ...


@runtime_checkable
class RerankerPort(Protocol):
    """Port for cross-attention or lexical-semantic reranking."""

    def rerank(
        self,
        query: str,
        passages: list[tuple[Any, str]],
        top_k: int = 5,
    ) -> list[tuple[Any, float]]:
        """Rerank passages against query returning sorted (item, score)."""
        ...


@runtime_checkable
class LLMProviderPort(Protocol):
    """Port for grounded answer synthesis."""

    async def synthesize(
        self,
        system_instruction: str,
        untrusted_evidence_prompt: str,
        call_timeout: float = 20.0,
    ) -> str:
        """Synthesize a grounded answer using the provided evidence."""
        ...
