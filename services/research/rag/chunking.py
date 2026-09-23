"""Deterministic Document Chunking for TrustTrip Phase 8 RAG.

Splits document text into sentence-aware, overlapping chunks with deterministic IDs.
"""
from __future__ import annotations

import hashlib
import re
from uuid import UUID, uuid5, NAMESPACE_DNS
from domain.research import RagChunk


def chunk_text(
    document_id: UUID,
    user_id: UUID,
    text: str,
    chunk_size: int = 600,
    overlap: int = 100,
) -> list[RagChunk]:
    """Deterministically partition text into bounded chunks with sentence boundaries."""
    if not text or not text.strip():
        return []

    # Clean whitespace and normalize newlines
    normalized_text = re.sub(r"\r\n|\r", "\n", text.strip())
    # Split by paragraphs or sentences
    paragraphs = [p.strip() for p in normalized_text.split("\n\n") if p.strip()]

    raw_passages: list[str] = []
    for para in paragraphs:
        if len(para) <= chunk_size:
            raw_passages.append(para)
        else:
            # Split paragraph by sentence boundaries
            sentences = re.split(r"(?<=[.!?])\s+", para)
            current_chunk = ""
            for sentence in sentences:
                if len(current_chunk) + len(sentence) + 1 <= chunk_size:
                    current_chunk = f"{current_chunk} {sentence}".strip()
                else:
                    if current_chunk:
                        raw_passages.append(current_chunk)
                    if len(sentence) > chunk_size:
                        # Hard slice long sentence
                        for i in range(0, len(sentence), chunk_size - overlap):
                            raw_passages.append(sentence[i : i + chunk_size])
                        current_chunk = ""
                    else:
                        current_chunk = sentence
            if current_chunk:
                raw_passages.append(current_chunk)

    # Combine into overlapping chunks
    chunks: list[RagChunk] = []
    chunk_index = 0
    buffer = ""

    for passage in raw_passages:
        if not buffer:
            buffer = passage
        elif len(buffer) + len(passage) + 1 <= chunk_size:
            buffer = f"{buffer}\n{passage}"
        else:
            content = buffer.strip()
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            chunk_id = uuid5(NAMESPACE_DNS, f"{document_id}:{chunk_index}:{content_hash[:16]}")
            approx_tokens = max(1, len(content.split()))

            chunks.append(
                RagChunk(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    user_id=user_id,
                    chunk_index=chunk_index,
                    text_content=content,
                    token_count=approx_tokens,
                    content_hash=content_hash,
                    metadata={"chunk_index": chunk_index},
                )
            )
            chunk_index += 1

            # Keep overlap from the end of buffer
            if overlap > 0 and len(buffer) > overlap:
                overlap_text = buffer[-overlap:].strip()
                buffer = f"{overlap_text}\n{passage}"
            else:
                buffer = passage

    if buffer.strip():
        content = buffer.strip()
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        chunk_id = uuid5(NAMESPACE_DNS, f"{document_id}:{chunk_index}:{content_hash[:16]}")
        approx_tokens = max(1, len(content.split()))

        chunks.append(
            RagChunk(
                chunk_id=chunk_id,
                document_id=document_id,
                user_id=user_id,
                chunk_index=chunk_index,
                text_content=content,
                token_count=approx_tokens,
                content_hash=content_hash,
                metadata={"chunk_index": chunk_index},
            )
        )

    return chunks


class DeterministicChunker:
    """Configurable text chunker for RAG ingestion."""

    def __init__(self, chunk_size: int = 600, chunk_overlap: int = 100):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk_document(
        self,
        doc_id: UUID,
        user_id: UUID,
        text: str,
        doc_hash: str = "",
    ) -> list[RagChunk]:
        return chunk_text(
            document_id=doc_id,
            user_id=user_id,
            text=text,
            chunk_size=self.chunk_size,
            overlap=self.chunk_overlap,
        )
