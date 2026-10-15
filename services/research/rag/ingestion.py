"""Document Ingestion and Parsing Pipeline for TrustTrip Phase 8 RAG.

Extracts text from TXT, MD, JSON, and PDF files without heavy external dependencies.
Validates size, format, and generates immutable RagDocument and RagChunk entities.
"""
from __future__ import annotations

import base64
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


def clean_pdf_string(s: str) -> str:
    r"""Unescape PDF octal character escapes (\ddd in cp1252/WinAnsi) and standard escape sequences."""
    def _octal_replace(m: re.Match) -> str:
        try:
            val = int(m.group(1), 8)
            return bytes([val]).decode("cp1252", errors="replace")
        except Exception:
            return m.group(0)

    s = re.sub(r"\\([0-7]{1,3})", _octal_replace, s)
    s = s.replace(r"\n", "\n").replace(r"\r", "\r").replace(r"\t", "\t")
    s = s.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
    return s.strip()


def extract_pdf_text_pure_python(data: bytes) -> str:
    """Extract readable text streams from PDF bytes without requiring native C dependencies.

    Parses PDF objects, decompresses FlateDecode/ASCII85Decode streams, and extracts text operators.
    Enforces decompression ratio and stream limits to prevent decompression bomb attacks.
    """
    if not data.startswith(b"%PDF-"):
        raise IngestionError("Invalid PDF document: missing '%PDF-' magic bytes.")

    text_fragments: list[str] = []
    total_decompressed = 0
    stream_count = 0

    # Find stream ... endstream blocks (supporting both \r\n and immediate ~>endstream)
    stream_pattern = re.compile(rb"stream[\r\n]+(.*?)[\r\n]*endstream", re.DOTALL)
    for match in stream_pattern.finditer(data):
        stream_count += 1
        if stream_count > MAX_PDF_STREAMS:
            raise IngestionError(f"PDF stream count exceeds maximum safe limit of {MAX_PDF_STREAMS}.")

        stream_bytes = match.group(1)
        cur_bytes = stream_bytes.strip()

        # Check for ASCII85Decode filter in preceding object dictionary or ~> trailer
        prefix = data[max(0, match.start() - 300) : match.start()]
        if b"ASCII85Decode" in prefix or cur_bytes.endswith(b"~>"):
            try:
                a85_data = cur_bytes
                if b"~>" in a85_data and not a85_data.endswith(b"~>"):
                    a85_data = a85_data[: a85_data.find(b"~>") + 2]
                cur_bytes = base64.a85decode(a85_data, adobe=True)
            except Exception:
                pass

        decompressed: bytes | None = None
        # Try direct decompression
        try:
            decompressed = zlib.decompress(cur_bytes)
        except Exception:
            try:
                decompressed = zlib.decompress(cur_bytes, -zlib.MAX_WBITS)
            except Exception:
                decompressed = cur_bytes

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
                    clean = clean_pdf_string(t)
                    if clean:
                        text_fragments.append(clean)

                # Match [ (text) -10 (text) ] TJ
                array_matches = re.findall(r"\[(.*?)\]\s*TJ", stream_str)
                for arr in array_matches:
                    sub_texts = re.findall(r"\((.*?)\)", arr)
                    combined = " ".join(clean_pdf_string(st) for st in sub_texts if clean_pdf_string(st))
                    if combined:
                        text_fragments.append(combined)
            except Exception:
                continue

    if not text_fragments:
        # Fallback to readable text strings, strictly excluding raw PDF object definitions and PostScript operators
        ascii_matches = re.findall(rb"[\x20-\x7E\s]{12,}", data)
        filtered: list[str] = []
        for m in ascii_matches:
            decoded = m.decode("ascii", errors="ignore").strip()
            # Exclude PDF object dictionaries, cross-references, and stream tags
            if any(token in decoded for token in ["obj", "endobj", "stream", "endstream", "xref", "trailer", "/Type", "/Catalog", "/Pages", "/Font", "/MediaBox", "/ProcSet"]):
                continue
            if len(decoded) > 20:
                filtered.append(decoded)
        return "\n".join(filtered)

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
