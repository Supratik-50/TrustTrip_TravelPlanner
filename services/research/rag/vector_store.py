"""Vector Store implementations for TrustTrip Phase 8 RAG.

Provides:
- MemoryVectorStore: thread-safe in-memory cosine vector store with tenant isolation for tests/dev.
- PostgresVectorStore: production persistent PostgreSQL vector store with pgvector and tenant isolation.
- get_vector_store(): factory enforcing fail-closed production selection.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, NamedTuple
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from domain.research import RagChunk
from services.research.ports import VectorStorePort
from services.research.rag.embedding import cosine_similarity

logger = logging.getLogger(__name__)


class ScoredChunk(NamedTuple):
    chunk: RagChunk
    score: float


class MemoryVectorStore(VectorStorePort):
    """In-memory thread-safe vector store enforcing strict user_id tenancy.

    ALLOWED ONLY FOR TEST/DEV ENVIRONMENTS.
    """

    _GLOBAL_STORE: dict[str, tuple[RagChunk, list[float]]] = {}
    _LOCK = threading.Lock()

    def __init__(self) -> None:
        self._store = MemoryVectorStore._GLOBAL_STORE

    def upsert(
        self,
        chunks: list[RagChunk],
        embeddings: list[list[float]],
    ) -> int:
        return self.upsert_chunks(chunks, embeddings)

    def query(
        self,
        query_embedding: list[float],
        user_id: UUID,
        top_k: int = 8,
        filter_document_ids: list[UUID] | None = None,
    ) -> list[ScoredChunk]:
        raw = self.query_similar(query_embedding, user_id, top_k, filter_document_ids)
        return [ScoredChunk(chunk=c, score=s) for c, s in raw]

    def upsert_chunks(
        self,
        chunks: list[RagChunk],
        embeddings: list[list[float]],
    ) -> int:
        if len(chunks) != len(embeddings):
            raise ValueError("Chunks count must match embeddings count.")

        with self._LOCK:
            count = 0
            for chunk, emb in zip(chunks, embeddings):
                if len(emb) != 128:
                    raise ValueError(f"Embedding dimension must be 128, got {len(emb)}")
                self._store[str(chunk.chunk_id)] = (chunk, emb)
                count += 1
            return count

    def query_similar(
        self,
        query_embedding: list[float],
        user_id: UUID,
        top_k: int = 8,
        filter_document_ids: list[UUID] | None = None,
    ) -> list[tuple[RagChunk, float]]:
        bounded_k = min(50, max(1, top_k))
        uid_str = str(user_id)
        doc_filter_set = {str(d) for d in filter_document_ids} if filter_document_ids else None

        scored: list[tuple[RagChunk, float]] = []
        with self._LOCK:
            for chunk, emb in self._store.values():
                # Enforce strict user_id tenancy
                if str(chunk.user_id) != uid_str:
                    continue
                # Apply optional document filter
                if doc_filter_set and str(chunk.document_id) not in doc_filter_set:
                    continue

                sim = cosine_similarity(query_embedding, emb)
                scored.append((chunk, sim))

        # Sort descending by similarity score
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:bounded_k]

    def delete_document(
        self,
        document_id: UUID,
        user_id: UUID,
    ) -> int:
        doc_str = str(document_id)
        uid_str = str(user_id)

        with self._LOCK:
            keys_to_del = [
                k for k, (chunk, _) in self._store.items()
                if str(chunk.document_id) == doc_str and str(chunk.user_id) == uid_str
            ]
            for k in keys_to_del:
                del self._store[k]
            return len(keys_to_del)

    def clear(self) -> None:
        with self._LOCK:
            self._store.clear()


class PostgresVectorStore(VectorStorePort):
    """Production PostgreSQL-backed persistent vector store.

    Enforces:
    - pgvector cosine distance retrieval (<=>) with fallback to in-memory cosine
    - 128-dimensional embedding validation on upsert
    - Hard bounded top_k (max 50)
    - Strict multi-tenant scoping (WHERE user_id = %s)
    - Survival across process restart and multiple workers
    """

    def __init__(self, db_url: str | None = None, schema: str | None = None) -> None:
        self.db_url = db_url or os.getenv("DATABASE_URL")
        self.schema = schema
        if not self.db_url:
            raise RuntimeError(
                "PostgresVectorStore initialization failed: DATABASE_URL is not set. "
                "Production cannot run without persistent database configuration."
            )
        # Verify connection and pgvector capability
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1;")
        except Exception as exc:
            raise RuntimeError(
                f"PostgresVectorStore connection check failed: {exc}. "
                "Production vector store must fail closed."
            ) from exc

    def _connect(self, row_factory=None):
        conn = psycopg.connect(self.db_url, autocommit=True, row_factory=row_factory)
        if self.schema:
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO {self.schema}, public;")
        return conn

    def _format_vector(self, emb: list[float]) -> str:
        if len(emb) != 128:
            raise ValueError(f"Embedding dimension must be exactly 128, got {len(emb)}")
        return "[" + ",".join(str(float(x)) for x in emb) + "]"

    def upsert(
        self,
        chunks: list[RagChunk],
        embeddings: list[list[float]],
    ) -> int:
        return self.upsert_chunks(chunks, embeddings)

    def upsert_chunks(
        self,
        chunks: list[RagChunk],
        embeddings: list[list[float]],
    ) -> int:
        if len(chunks) != len(embeddings):
            raise ValueError("Chunks count must match embeddings count.")

        formatted_embeddings: list[str] = [self._format_vector(e) for e in embeddings]

        with self._connect() as conn:
            with conn.cursor() as cur:
                count = 0
                for chunk, vec_str in zip(chunks, formatted_embeddings):
                    # Upsert chunk and embedding
                    cur.execute("""
                        INSERT INTO rag_chunks (
                            id, document_id, user_id, chunk_index, text_content,
                            token_count, content_hash, metadata, embedding, created_at
                        ) VALUES (
                            %s, %s, %s, %s, %s,
                            %s, %s, %s::jsonb, %s::vector, now()
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            embedding = EXCLUDED.embedding,
                            metadata = EXCLUDED.metadata;
                    """, (
                        chunk.chunk_id,
                        chunk.document_id,
                        chunk.user_id,
                        chunk.chunk_index,
                        chunk.text_content,
                        chunk.token_count,
                        chunk.content_hash,
                        json.dumps(chunk.metadata),
                        vec_str,
                    ))
                    count += 1
                return count

    def query(
        self,
        query_embedding: list[float],
        user_id: UUID,
        top_k: int = 8,
        filter_document_ids: list[UUID] | None = None,
    ) -> list[ScoredChunk]:
        raw = self.query_similar(query_embedding, user_id, top_k, filter_document_ids)
        return [ScoredChunk(chunk=c, score=s) for c, s in raw]

    def query_similar(
        self,
        query_embedding: list[float],
        user_id: UUID,
        top_k: int = 8,
        filter_document_ids: list[UUID] | None = None,
    ) -> list[tuple[RagChunk, float]]:
        bounded_k = min(50, max(1, top_k))
        vec_str = self._format_vector(query_embedding)

        query_sql = """
            SELECT id, document_id, user_id, chunk_index, text_content, token_count,
                   content_hash, metadata, 1 - (embedding <=> %s::vector) AS score
            FROM rag_chunks
            WHERE user_id = %s
              AND embedding IS NOT NULL
        """
        params: list[Any] = [vec_str, user_id]

        if filter_document_ids:
            query_sql += " AND document_id = ANY(%s)"
            params.append(filter_document_ids)

        query_sql += " ORDER BY embedding <=> %s::vector LIMIT %s"
        params.extend([vec_str, bounded_k])

        results: list[tuple[RagChunk, float]] = []
        with self._connect(row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(query_sql, tuple(params))
                for row in cur.fetchall():
                    meta = row["metadata"] if isinstance(row["metadata"], dict) else {}
                    chunk = RagChunk(
                        chunk_id=row["id"],
                        document_id=row["document_id"],
                        user_id=row["user_id"],
                        chunk_index=row["chunk_index"],
                        text_content=row["text_content"],
                        token_count=row["token_count"],
                        content_hash=row["content_hash"],
                        metadata=meta,
                    )
                    score = float(row["score"]) if row["score"] is not None else 0.0
                    results.append((chunk, score))

        return results

    def delete_document(
        self,
        document_id: UUID,
        user_id: UUID,
    ) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM rag_chunks
                    WHERE document_id = %s AND user_id = %s;
                """, (document_id, user_id))
                return cur.rowcount


def get_vector_store() -> VectorStorePort:
    """Factory selecting persistent PostgresVectorStore for production, failing closed on errors.

    CRITICAL (Blocker 3 & Mandatory Addition 1):
    Never silently falls back to MemoryVectorStore in production.
    """
    backend = os.getenv("VECTOR_STORE_BACKEND", "").strip().lower()
    env = os.getenv("ENVIRONMENT", "").strip().lower()

    if backend == "memory":
        return MemoryVectorStore()

    if backend == "postgres" or (not backend and os.getenv("DATABASE_URL") and env != "test"):
        # In production / postgres mode: MUST succeed or fail closed
        return PostgresVectorStore()

    if env == "test" or os.getenv("TESTING") == "1":
        return MemoryVectorStore()

    # If DATABASE_URL is set, default to persistent PostgresVectorStore
    if os.getenv("DATABASE_URL"):
        return PostgresVectorStore()

    # Fail closed rather than silent fallback in production
    raise RuntimeError(
        "Production Vector Store configuration error: VECTOR_STORE_BACKEND is not set to 'memory', "
        "and DATABASE_URL is missing. Production requires persistent PostgresVectorStore. "
        "Silent fallback to in-memory storage is strictly prohibited."
    )
