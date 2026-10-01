"""Database and In-Memory Persistence Repository for Phase 8 Research.

Manages sessions, queries, sources, evidence, citations, and RAG documents/chunks.
Enforces strict tenant isolation: all queries require user_id scoping.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4
import psycopg
from psycopg.rows import dict_row

from domain.research import (
    RagChunk,
    RagDocument,
    ResearchCitation,
    ResearchEvidence,
    ResearchFreshnessState,
    ResearchIntent,
    ResearchMode,
    ResearchQuery,
    ResearchSession,
    ResearchSource,
    SourceTrustClass,
    SourceType,
)


def _dsn() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required for research database persistence.")
    if "sslmode=" not in value:
        value += ("&" if "?" in value else "?") + "sslmode=require"
    return value


class ResearchStore:
    """Repository managing research sessions, queries, evidence, citations, and RAG documents."""

    _IN_MEM_SESSIONS: dict[str, ResearchSession] = {}
    _IN_MEM_QUERIES: dict[str, ResearchQuery] = {}
    _IN_MEM_SOURCES: dict[str, ResearchSource] = {}
    _IN_MEM_EVIDENCE: dict[str, ResearchEvidence] = {}
    _IN_MEM_CITATIONS: dict[str, list[ResearchCitation]] = {}
    _IN_MEM_DOCS: dict[str, RagDocument] = {}
    _IN_MEM_CHUNKS: dict[str, list[RagChunk]] = {}
    _LOCK = threading.Lock()

    def __init__(self) -> None:
        self.use_in_memory = bool(os.getenv("USE_IN_MEMORY_AUTH"))

    def _connect(self):
        return psycopg.connect(_dsn(), row_factory=dict_row, autocommit=True)

    # ==================== SESSIONS ====================

    def save_session(self, session: ResearchSession) -> ResearchSession:
        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            with self._LOCK:
                self._IN_MEM_SESSIONS[str(session.session_id)] = session
                return session

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_sessions (id, user_id, trip_id, mode, status, created_at, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    mode = EXCLUDED.mode,
                    status = EXCLUDED.status,
                    expires_at = EXCLUDED.expires_at
                """,
                (
                    str(session.session_id),
                    str(session.user_id),
                    str(session.trip_id) if session.trip_id else None,
                    session.mode.value,
                    session.status,
                    session.created_at,
                    session.expires_at,
                ),
            )
            return session

    def get_session(self, session_id: UUID, user_id: UUID) -> ResearchSession | None:
        sid_str = str(session_id)
        uid_str = str(user_id)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            with self._LOCK:
                sess = self._IN_MEM_SESSIONS.get(sid_str)
                if sess and str(sess.user_id) == uid_str:
                    return sess
                return None

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_sessions WHERE id = %s AND user_id = %s",
                (sid_str, uid_str),
            ).fetchone()
            if not row:
                return None
            return ResearchSession(
                session_id=UUID(str(row["id"])),
                user_id=UUID(str(row["user_id"])),
                trip_id=UUID(str(row["trip_id"])) if row["trip_id"] else None,
                mode=ResearchMode(row["mode"]),
                status=row["status"],
                created_at=row["created_at"],
                expires_at=row["expires_at"],
            )

    # ==================== QUERIES & SOURCES ====================

    def save_query(self, query: ResearchQuery) -> ResearchQuery:
        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            with self._LOCK:
                self._IN_MEM_QUERIES[str(query.query_id)] = query
                return query

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_queries (
                    id, session_id, user_id, trip_id, revision_number,
                    query_text, normalized_query, intent, mode, locale, max_sources, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(query.query_id),
                    str(query.session_id),
                    str(query.user_id),
                    str(query.trip_id) if query.trip_id else None,
                    query.revision_number,
                    query.query_text,
                    query.normalized_query,
                    query.intent.value,
                    query.mode.value,
                    query.locale,
                    query.max_sources,
                    query.created_at,
                ),
            )
            return query

    def save_source(self, user_id: UUID, source: ResearchSource) -> ResearchSource:
        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            with self._LOCK:
                self._IN_MEM_SOURCES[str(source.source_id)] = source
                return source

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_sources (
                    id, user_id, url, canonical_url, title, domain,
                    source_type, trust_class, published_at, retrieved_at, content_hash, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    str(source.source_id),
                    str(user_id),
                    source.url,
                    source.canonical_url,
                    source.title,
                    source.domain,
                    source.source_type.value,
                    source.trust_class.value,
                    source.published_at,
                    source.retrieved_at,
                    source.content_hash,
                    json.dumps(source.metadata),
                ),
            )
            return source

    # ==================== EVIDENCE & CITATIONS ====================

    def save_evidence_batch(self, evidence_list: list[ResearchEvidence]) -> None:
        if not evidence_list:
            return

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            with self._LOCK:
                for ev in evidence_list:
                    self._IN_MEM_EVIDENCE[str(ev.evidence_id)] = ev
                return

        with self._connect() as conn:
            for ev in evidence_list:
                conn.execute(
                    """
                    INSERT INTO research_evidence (
                        id, query_id, user_id, source_id, trip_id, revision_id,
                        canonical_url, title, domain,
                        trust_class, claims, snippet, locator, relevance_score,
                        freshness_state, retrieved_at, published_at, immutable_hash
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        str(ev.evidence_id),
                        str(ev.query_id),
                        str(ev.user_id),
                        str(ev.source_id),
                        str(ev.trip_id) if ev.trip_id else None,
                        str(ev.revision_id) if ev.revision_id else None,
                        ev.canonical_url,
                        ev.title,
                        ev.domain,
                        ev.trust_class.value,
                        json.dumps(ev.claims),
                        ev.snippet,
                        ev.locator,
                        ev.relevance_score,
                        ev.freshness_state.value,
                        ev.retrieved_at,
                        ev.published_at,
                        ev.immutable_hash,
                    ),
                )

    def get_evidence(self, evidence_id: UUID) -> ResearchEvidence | None:
        eid_str = str(evidence_id)
        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            with self._LOCK:
                return self._IN_MEM_EVIDENCE.get(eid_str)

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_evidence WHERE id = %s",
                (eid_str,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_evidence(row)

    def get_evidence_for_query(self, query_id: UUID) -> list[ResearchEvidence]:
        qid_str = str(query_id)
        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            with self._LOCK:
                return [ev for ev in self._IN_MEM_EVIDENCE.values() if str(ev.query_id) == qid_str]

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM research_evidence WHERE query_id = %s ORDER BY retrieved_at ASC",
                (qid_str,),
            ).fetchall()
            return [self._row_to_evidence(r) for r in rows]

    def _row_to_evidence(self, row: dict[str, Any]) -> ResearchEvidence:
        claims = row.get("claims")
        if isinstance(claims, str):
            try:
                claims = json.loads(claims)
            except Exception:
                claims = []
        elif not isinstance(claims, list):
            claims = []

        rel_score = row.get("relevance_score")
        if rel_score is not None:
            rel_score = float(rel_score)

        return ResearchEvidence(
            evidence_id=UUID(str(row["id"])),
            query_id=UUID(str(row["query_id"])),
            user_id=UUID(str(row["user_id"])) if row.get("user_id") else None,
            source_id=UUID(str(row["source_id"])),
            trip_id=UUID(str(row["trip_id"])) if row.get("trip_id") else None,
            revision_id=UUID(str(row["revision_id"])) if row.get("revision_id") else None,
            canonical_url=row.get("canonical_url", ""),
            title=row.get("title"),
            domain=row.get("domain", ""),
            trust_class=SourceTrustClass(row.get("trust_class", SourceTrustClass.UNKNOWN.value)),
            claims=claims,
            snippet=row.get("snippet"),
            locator=row.get("locator"),
            relevance_score=rel_score,
            freshness_state=ResearchFreshnessState(row.get("freshness_state", ResearchFreshnessState.FRESH.value)),
            retrieved_at=row.get("retrieved_at"),
            published_at=row.get("published_at"),
            immutable_hash=row.get("immutable_hash", ""),
        )

    def save_citations(self, query_id: UUID, user_id: UUID, citations: list[ResearchCitation]) -> None:
        qid_str = str(query_id)
        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            with self._LOCK:
                self._IN_MEM_CITATIONS[qid_str] = citations
                return

        with self._connect() as conn:
            for c in citations:
                conn.execute(
                    """
                    INSERT INTO research_citations (
                        id, query_id, evidence_id, user_id, citation_token,
                        title, domain, canonical_url, locator, cited_claims, retrieved_at, published_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    """,
                    (
                        str(uuid4()),
                        qid_str,
                        str(c.evidence_id),
                        str(user_id),
                        c.citation_id,
                        c.title,
                        c.domain,
                        c.canonical_url,
                        c.locator,
                        json.dumps(c.cited_claims),
                        c.retrieved_at,
                        c.published_at,
                    ),
                )

    def get_citations(self, query_id: UUID) -> list[ResearchCitation]:
        qid_str = str(query_id)
        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            with self._LOCK:
                return list(self._IN_MEM_CITATIONS.get(qid_str, []))

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM research_citations WHERE query_id = %s ORDER BY citation_token ASC",
                (qid_str,),
            ).fetchall()
            return [
                ResearchCitation(
                    citation_id=r["citation_token"],
                    evidence_id=UUID(str(r["evidence_id"])),
                    title=r.get("title"),
                    domain=r.get("domain", ""),
                    canonical_url=r.get("canonical_url", ""),
                    published_at=r.get("published_at"),
                    retrieved_at=r.get("retrieved_at"),
                    locator=r.get("locator"),
                    cited_claims=json.loads(r["cited_claims"]) if isinstance(r.get("cited_claims"), str) else (r.get("cited_claims") or []),
                )
                for r in rows
            ]

    # ==================== RAG DOCUMENTS & CHUNKS ====================

    def save_document(self, doc: RagDocument, chunks: list[RagChunk]) -> None:
        doc_str = str(doc.document_id)
        uid_str = str(doc.user_id)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            with self._LOCK:
                self._IN_MEM_DOCS[doc_str] = doc
                self._IN_MEM_CHUNKS[doc_str] = chunks
                return

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO rag_documents (
                    id, user_id, trip_id, title, filename, content_type,
                    file_size_bytes, content_hash, chunk_count, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    doc_str,
                    uid_str,
                    str(doc.trip_id) if doc.trip_id else None,
                    doc.title,
                    doc.filename,
                    doc.content_type,
                    doc.file_size_bytes,
                    doc.content_hash,
                    doc.chunk_count,
                    doc.created_at,
                ),
            )
            for c in chunks:
                conn.execute(
                    """
                    INSERT INTO rag_chunks (
                        id, document_id, user_id, chunk_index, text_content,
                        token_count, content_hash, metadata, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
                    """,
                    (
                        str(c.chunk_id),
                        doc_str,
                        uid_str,
                        c.chunk_index,
                        c.text_content,
                        c.token_count,
                        c.content_hash,
                        json.dumps(c.metadata),
                    ),
                )

    def list_documents(self, user_id: UUID) -> list[RagDocument]:
        uid_str = str(user_id)
        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            with self._LOCK:
                docs = [d for d in self._IN_MEM_DOCS.values() if str(d.user_id) == uid_str]
                docs.sort(key=lambda d: d.created_at, reverse=True)
                return docs

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM rag_documents WHERE user_id = %s ORDER BY created_at DESC",
                (uid_str,),
            ).fetchall()
            return [
                RagDocument(
                    document_id=UUID(str(r["id"])),
                    user_id=UUID(str(r["user_id"])),
                    trip_id=UUID(str(r["trip_id"])) if r["trip_id"] else None,
                    title=r["title"],
                    filename=r["filename"],
                    content_type=r["content_type"],
                    file_size_bytes=r["file_size_bytes"],
                    content_hash=r["content_hash"],
                    chunk_count=r["chunk_count"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    def delete_document(self, document_id: UUID, user_id: UUID) -> bool:
        doc_str = str(document_id)
        uid_str = str(user_id)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            with self._LOCK:
                if doc_str in self._IN_MEM_DOCS and str(self._IN_MEM_DOCS[doc_str].user_id) == uid_str:
                    del self._IN_MEM_DOCS[doc_str]
                    self._IN_MEM_CHUNKS.pop(doc_str, None)
                    return True
                return False

        with self._connect() as conn:
            # Cascade delete in database via ON DELETE CASCADE or explicit
            row = conn.execute(
                "DELETE FROM rag_documents WHERE id = %s AND user_id = %s RETURNING id",
                (doc_str, uid_str),
            ).fetchone()
            return bool(row)

    def get_document(self, document_id: UUID, user_id: UUID | None = None) -> RagDocument | None:
        doc_str = str(document_id)
        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            with self._LOCK:
                doc = self._IN_MEM_DOCS.get(doc_str)
                if not doc:
                    return None
                if user_id and str(doc.user_id) != str(user_id):
                    return None
                return doc

        with self._connect() as conn:
            if user_id:
                row = conn.execute(
                    "SELECT * FROM rag_documents WHERE id = %s AND user_id = %s",
                    (doc_str, str(user_id)),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM rag_documents WHERE id = %s",
                    (doc_str,),
                ).fetchone()
            if not row:
                return None
            return RagDocument(
                document_id=UUID(str(row["id"])),
                user_id=UUID(str(row["user_id"])),
                trip_id=UUID(str(row["trip_id"])) if row["trip_id"] else None,
                title=row["title"],
                filename=row["filename"],
                content_type=row["content_type"],
                file_size_bytes=row["file_size_bytes"],
                content_hash=row["content_hash"],
                chunk_count=row["chunk_count"],
                created_at=row["created_at"],
            )

    def clear(self) -> None:
        with self._LOCK:
            self._IN_MEM_SESSIONS.clear()
            self._IN_MEM_QUERIES.clear()
            self._IN_MEM_SOURCES.clear()
            self._IN_MEM_EVIDENCE.clear()
            self._IN_MEM_CITATIONS.clear()
            self._IN_MEM_DOCS.clear()
            self._IN_MEM_CHUNKS.clear()


class InMemoryResearchStore(ResearchStore):
    """In-memory research store providing both sync and async helper methods for tests."""

    def __init__(self) -> None:
        super().__init__()
        self.use_in_memory = True

    async def create_rag_document(self, doc: RagDocument, chunks: list[RagChunk] | None = None) -> None:
        self.save_document(doc, chunks or [])

    async def list_rag_documents(self, user_id: UUID) -> list[RagDocument]:
        return self.list_documents(user_id)

    async def get_rag_document(self, document_id: UUID, user_id: UUID | None = None) -> RagDocument | None:
        return self.get_document(document_id, user_id=user_id)
