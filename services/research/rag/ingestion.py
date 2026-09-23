"""Document Ingestion and Parsing Pipeline for TrustTrip Phase 8 RAG.

Extracts text from TXT, MD, JSON, and PDF files without heavy external dependencies.
Validates size, format, and generates immutable RagDocument and RagChunk entities.
"""
from __future__ import annotations

import hashlib
import json
import re
import zlib
from uuid import UUID, uuid4
from domain.research import RagDocument, RagChunk
from services.research.rag.chunking import chunk_text

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024  # 10 MB limit
MAX_DECOMPRESSED_PDF_BYTES = 10 * 1024 * 1024  # 10 MB decompression bomb limit
MAX_EXTRACTED_TEXT_CHARS = 2_000_000  # 2M characters limit
MAX_CHUNKS = 500
MAX_PDF_STREAMS = 200


class IngestionError(Exception):
    """Raised when document ingestion or parsing fails."""
    pass


def extract_pdf_text_pure_python(data: bytes) -> str:
    """Extract readable text streams from PDF bytes without requiring native C dependencies.

    Parses PDF objects, decompresses FlateDecode streams with zlib, and extracts text operators.
    Enforces decompression ratio and stream limits to prevent decompression bomb attacks.
    """
    if not data.startswith(b"%PDF-"):
        raise IngestionError("Invalid PDF document: missing '%PDF-' magic bytes.")

    text_fragments: list[str] = []
    total_decompressed = 0
    stream_count = 0

    # Find stream ... endstream blocks
    stream_pattern = re.compile(b"stream[\r\n]+(.*?)[\r\n]+endstream", re.DOTALL)
    for match in stream_pattern.finditer(data):
        stream_count += 1
        if stream_count > MAX_PDF_STREAMS:
            raise IngestionError(f"PDF stream count exceeds maximum safe limit of {MAX_PDF_STREAMS}.")

        stream_bytes = match.group(1)
        decompressed: bytes | None = None
        # Try direct decompression
        try:
            decompressed = zlib.decompress(stream_bytes)
        except Exception:
            try:
                decompressed = zlib.decompress(stream_bytes, -zlib.MAX_WBITS)
            except Exception:
                decompressed = stream_bytes

        if decompressed:
            total_decompressed += len(decompressed)
            # Decompression bomb defense (Blocker 7)
            if total_decompressed > MAX_DECOMPRESSED_PDF_BYTES:
                raise IngestionError(
                    f"PDF decompressed bytes ({total_decompressed}) exceeded safe limit of {MAX_DECOMPRESSED_PDF_BYTES} bytes."
                )

            try:
                stream_str = decompressed.decode("latin1", errors="ignore")
                # Match (text) Tj
                tj_matches = re.findall(r"\((.*?)\)\s*Tj", stream_str)
                for t in tj_matches:
                    clean = re.sub(r"\\([\\()])", r"\1", t).strip()
                    if clean:
                        text_fragments.append(clean)

                # Match [ (text) -10 (text) ] TJ
                array_matches = re.findall(r"\[(.*?)\]\s*TJ", stream_str)
                for arr in array_matches:
                    sub_texts = re.findall(r"\((.*?)\)", arr)
                    combined = " ".join(re.sub(r"\\([\\()])", r"\1", st).strip() for st in sub_texts if st.strip())
                    if combined:
                        text_fragments.append(combined)
            except Exception:
                continue

    if not text_fragments:
        # Fallback to ASCII text string extraction
        ascii_matches = re.findall(rb"[\x20-\x7E\s]{4,}", data)
        return "\n".join(m.decode("ascii", errors="ignore").strip() for m in ascii_matches if len(m.strip()) > 10)

    return "\n".join(text_fragments)


def ingest_document(
    user_id: UUID,
    filename: str,
    raw_bytes: bytes,
    content_type: str | None = None,
    trip_id: UUID | None = None,
    title: str | None = None,
    chunk_size: int = 600,
    overlap: int = 100,
) -> tuple[RagDocument, list[RagChunk]]:
    """Validate, parse, extract, and chunk an uploaded document."""
    if not raw_bytes:
        raise IngestionError("Uploaded document is empty.")

    if len(raw_bytes) > MAX_DOCUMENT_BYTES:
        raise IngestionError(f"Document size ({len(raw_bytes)} bytes) exceeds the maximum allowed 10MB limit.")

    doc_id = uuid4()
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    lower_name = filename.lower()
    inferred_type = content_type or "text/plain"

    # Extract text content based on format
    extracted_text = ""
    if lower_name.endswith(".pdf") or "pdf" in inferred_type:
        inferred_type = "application/pdf"
        try:
            extracted_text = extract_pdf_text_pure_python(raw_bytes)
        except IngestionError:
            raise
        except Exception as exc:
            raise IngestionError(f"Failed to parse PDF document: {exc}") from exc
    elif lower_name.endswith(".json") or "json" in inferred_type:
        inferred_type = "application/json"
        try:
            parsed_json = json.loads(raw_bytes.decode("utf-8", errors="replace"))
            extracted_text = json.dumps(parsed_json, indent=2)
        except Exception as exc:
            raise IngestionError(f"Invalid JSON document: {exc}") from exc
    elif lower_name.endswith(".md") or "markdown" in inferred_type:
        inferred_type = "text/markdown"
        extracted_text = raw_bytes.decode("utf-8", errors="replace")
    else:
        inferred_type = "text/plain"
        extracted_text = raw_bytes.decode("utf-8", errors="replace")

    clean_text = extracted_text.strip()
    if not clean_text:
        raise IngestionError("No readable text could be extracted from the document.")

    if len(clean_text) > MAX_EXTRACTED_TEXT_CHARS:
        raise IngestionError(
            f"Extracted document text ({len(clean_text)} characters) exceeds maximum limit of {MAX_EXTRACTED_TEXT_CHARS} characters."
        )

    doc_title = title or filename.rsplit(".", 1)[0]
    chunks = chunk_text(
        document_id=doc_id,
        user_id=user_id,
        text=clean_text,
        chunk_size=chunk_size,
        overlap=overlap,
    )

    if len(chunks) > MAX_CHUNKS:
        raise IngestionError(
            f"Document generated {len(chunks)} chunks, exceeding maximum allowed limit of {MAX_CHUNKS}."
        )

    doc_record = RagDocument(
        document_id=doc_id,
        user_id=user_id,
        trip_id=trip_id,
        title=doc_title[:500],
        filename=filename[:500],
        content_type=inferred_type[:100],
        file_size_bytes=len(raw_bytes),
        content_hash=content_hash,
        chunk_count=len(chunks),
    )

    return doc_record, chunks


class DocumentIngestionService:
    """Document ingestion service supporting chunker integration."""

    def __init__(self, chunker: Any = None) -> None:
        self.chunker = chunker

    def ingest(
        self,
        user_id: UUID,
        filename: str,
        content: bytes,
        content_type: str | None = None,
        trip_id: UUID | None = None,
        title: str | None = None,
    ) -> tuple[RagDocument, list[RagChunk]]:
        chunk_size = getattr(self.chunker, "chunk_size", 600)
        overlap = getattr(self.chunker, "chunk_overlap", 100)
        try:
            return ingest_document(
                user_id=user_id,
                filename=filename,
                raw_bytes=content,
                content_type=content_type,
                trip_id=trip_id,
                title=title,
                chunk_size=chunk_size,
                overlap=overlap,
            )
        except IngestionError as exc:
            raise ValueError(str(exc)) from exc
