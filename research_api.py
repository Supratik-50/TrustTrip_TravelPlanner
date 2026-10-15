"""FastAPI Router for TrustTrip Phase 8 Travel Research and RAG.

Endpoints:
- POST /api/research/query: Execute grounded research with source citations.
- POST /api/research/documents/upload: Upload private document for tenant RAG.
- GET /api/research/documents: List user's RAG documents.
- DELETE /api/research/documents/{doc_id}: Delete a private RAG document.
- GET /api/research/sessions/{session_id}: Retrieve research session metadata.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from dependencies.auth import get_current_user
from domain.research import (
    ResearchMode,
    ResearchResult,
)
from domain.user import UserResponse
from services.research.orchestrator import ResearchOrchestrator
from services.research.rag.embedding import get_embedding_provider
from services.research.rag.ingestion import MAX_DOCUMENT_BYTES, IngestionError, ingest_document
from services.research.rag.vector_store import get_vector_store
from services.research.research_store import ResearchStore
from services.trip_store import TripStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/research", tags=["research"])

_vector_store = get_vector_store()
_embedding_provider = get_embedding_provider()
_orchestrator = ResearchOrchestrator(vector_store=_vector_store, embedding_provider=_embedding_provider)
_research_store = ResearchStore()
_trip_store = TripStore()


class ResearchQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    mode: str = Field(default="auto")  # auto, web, rag, hybrid
    trip_id: UUID | None = None
    document_ids: list[UUID] = Field(default_factory=list)
    locale: str = Field(default="en-US", max_length=20)
    max_sources: int = Field(default=5, ge=1, le=20)


@router.post(
    "/query",
    summary="Execute grounded travel research query with verified citations",
    response_model=ResearchResult,
)
async def query_research(
    req: ResearchQueryRequest,
    current_user: UserResponse = Depends(get_current_user),
) -> ResearchResult:
    """Execute citation-grounded travel research across web and/or uploaded documents."""
    # Verify trip ownership if trip_id provided
    rev_num = None
    if req.trip_id:
        trip = _trip_store.get_trip(req.trip_id, current_user.id)
        if not trip:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Trip not found or does not belong to you.",
            )
        rev_num = trip.get("current_revision_number")

    # Authorize requested document_ids strictly (Blocker 14 / IDOR defense)
    if req.document_ids:
        user_docs = _research_store.list_documents(current_user.id)
        user_doc_ids = {d.document_id for d in user_docs}
        for d_id in req.document_ids:
            if d_id not in user_doc_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Access denied to document {d_id} or document not found.",
                )

    try:
        mode_enum = ResearchMode(req.mode.lower())
    except ValueError:
        mode_enum = ResearchMode.AUTO

    try:
        result = await _orchestrator.execute_research(
            user_id=current_user.id,
            query_text=req.query,
            mode=mode_enum,
            trip_id=req.trip_id,
            revision_number=rev_num,
            document_ids=req.document_ids,
            locale=req.locale,
            max_sources=req.max_sources,
        )
        return result
    except Exception as exc:
        logger.error("Research query execution error: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to execute research query.",
        )


def sanitize_upload_filename(raw_name: str | None, max_length: int = 128) -> str:
    """Enforce strict filename security policy:
    - Basename only (strip paths)
    - Reject/remove path traversal tokens (..)
    - Reject/remove control characters and non-printable characters
    - Enforce safe length while preserving extension
    - Preserve only safe normalized filename
    """
    import os
    if not raw_name:
        return "uploaded_doc.txt"
    name = os.path.basename(raw_name.strip())
    while ".." in name:
        name = name.replace("..", "")
    name = name.replace("/", "").replace("\\", "")
    name = "".join(
        c for c in name
        if c.isprintable() and ord(c) >= 32 and ord(c) != 127 and c not in '<>:"/\\|?*\0'
    )
    name = name.strip(". ")
    if not name:
        name = "uploaded_doc.txt"
    if len(name) > max_length:
        base, ext = os.path.splitext(name)
        ext = ext[:16]
        name = base[: max_length - len(ext)] + ext
    return name


@router.post(
    "/documents/upload",
    status_code=status.HTTP_201_CREATED,
    summary="Upload a private document (PDF, TXT, MD, JSON) for tenant RAG retrieval",
)
async def upload_document(
    file: UploadFile = File(...),
    trip_id: str | None = Form(default=None),
    title: str | None = Form(default=None),
    current_user: UserResponse = Depends(get_current_user),
) -> dict[str, Any]:
    """Upload and chunk a document for private RAG vector retrieval."""
    parsed_trip_id: UUID | None = None
    if trip_id and str(trip_id).strip() and str(trip_id).strip() not in ("null", "undefined"):
        try:
            parsed_trip_id = UUID(str(trip_id).strip())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid trip_id format.",
            )
        trip = _trip_store.get_trip(parsed_trip_id, current_user.id)
        if not trip:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Trip not found or does not belong to you.",
            )

    # 1. Check Content-Length header if present
    content_length = file.headers.get("content-length") if hasattr(file, "headers") and file.headers else None
    if content_length:
        try:
            if int(content_length) > MAX_DOCUMENT_BYTES:
                await file.close()
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Document exceeds maximum allowed size of 10MB.",
                )
        except ValueError:
            pass

    # 2. Stream file in chunks to enforce size limit while streaming, aborting before buffering
    chunks_list: list[bytes] = []
    total_size = 0
    chunk_size = 64 * 1024  # 64KB
    try:
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > MAX_DOCUMENT_BYTES:
                chunks_list.clear()
                await file.close()
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Document exceeds maximum allowed size of 10MB.",
                )
            chunks_list.append(chunk)
        raw_bytes = b"".join(chunks_list)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Failed to read uploaded file: %s", exc)
        await file.close()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to read uploaded file.",
        )
    finally:
        try:
            await file.close()
        except Exception:
            pass

    # 3. Sanitize filename (basename only, reject traversal, control chars, safe length)
    safe_filename = sanitize_upload_filename(file.filename)

    try:
        doc_record, chunks = ingest_document(
            user_id=current_user.id,
            filename=safe_filename,
            raw_bytes=raw_bytes,
            content_type=file.content_type,
            trip_id=parsed_trip_id,
            title=title,
        )
    except IngestionError as exc:
        logger.warning("Ingestion error for user %s: %s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document ingestion failed. Ensure document format and text encoding are valid.",
        )

    # 4. Embed and persist document and chunks atomically
    try:
        texts = [c.text_content for c in chunks]
        embeddings = _embedding_provider.embed_texts(texts)
        _research_store.save_document(doc_record, chunks=chunks, embeddings=embeddings)
        from services.research.rag.vector_store import MemoryVectorStore
        vstore = get_vector_store()
        if isinstance(vstore, MemoryVectorStore):
            vstore.upsert_chunks(chunks, embeddings)
    except Exception as exc:
        logger.error("Failed to index and store document for user %s: %s", current_user.id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to index document.",
        )

    return {
        "success": True,
        "document": doc_record.model_dump(),
        "chunks_indexed": len(chunks),
    }


@router.get(
    "/documents",
    summary="List all private RAG documents uploaded by authenticated user",
)
async def list_documents(
    current_user: UserResponse = Depends(get_current_user),
) -> dict[str, Any]:
    docs = _research_store.list_documents(current_user.id)
    return {
        "success": True,
        "documents": [d.model_dump() for d in docs],
    }


@router.delete(
    "/documents/{doc_id}",
    summary="Delete a private RAG document and cascade remove chunks and embeddings",
)
async def delete_document(
    doc_id: UUID,
    current_user: UserResponse = Depends(get_current_user),
) -> dict[str, Any]:
    deleted = _research_store.delete_document(doc_id, current_user.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found or does not belong to you.",
        )
    _vector_store.delete_document(doc_id, current_user.id)
    return {"success": True, "deleted": True}


@router.get(
    "/sessions/{session_id}",
    summary="Get research session metadata",
)
async def get_session(
    session_id: UUID,
    current_user: UserResponse = Depends(get_current_user),
) -> dict[str, Any]:
    sess = _research_store.get_session(session_id, current_user.id)
    if not sess:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research session not found or does not belong to you.",
        )
    return {"success": True, "session": sess.model_dump()}
