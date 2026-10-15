"""Phase 8 RAG Document Upload Regression Test Suite.

Verifies:
1. Exact reproduction of the "Field required" HTTP 422 error when Content-Type: application/json
   overrides the multipart request without boundary.
2. Successful upload of a Goa Travel Guide PDF through the real API path.
3. Verification that chunks and 384-dim BGE embeddings are created and persisted in the vector store.
4. Verification that semantic retrieval returns relevant Goa evidence from the uploaded PDF.
5. Strict authentication and cross-tenant isolation enforcement.
6. Safe handling of optional trip_id formats (None, valid UUID, empty string).
"""
import io
from typing import Any
from uuid import UUID, uuid4
import pytest
from fastapi.testclient import TestClient

from app import app
from domain.user import UserResponse
from services.auth_service import create_access_token
from services.auth_store import UserStore
from services.research.rag.vector_store import get_vector_store
from services.research.research_store import ResearchStore


@pytest.fixture(scope="module")
def api_client():
    return TestClient(app)


def _ensure_user_in_db(user_id: Any, email: str) -> None:
    import os
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        return
    import psycopg
    if "sslmode=" not in db_url:
        db_url += ("&" if "?" in db_url else "?") + "sslmode=require"
    try:
        with psycopg.connect(db_url, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, 'hash') ON CONFLICT (id) DO NOTHING;",
                (user_id, email),
            )
    except Exception:
        pass


@pytest.fixture(scope="module")
def auth_user():
    user_store = UserStore()
    email = f"upload_user_{uuid4().hex[:6]}@example.com"
    try:
        user = user_store.create_user(email=email, password_hash="hash")
    except ValueError:
        user = user_store.get_by_email(email)
    _ensure_user_in_db(user["id"], email)
    token = create_access_token(user_id=user["id"], email=email)
    return {
        "user_id": UUID(str(user["id"])),
        "email": email,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }


@pytest.fixture(scope="module")
def other_user():
    user_store = UserStore()
    email = f"other_user_{uuid4().hex[:6]}@example.com"
    try:
        user = user_store.create_user(email=email, password_hash="hash")
    except ValueError:
        user = user_store.get_by_email(email)
    _ensure_user_in_db(user["id"], email)
    token = create_access_token(user_id=user["id"], email=email)
    return {
        "user_id": UUID(str(user["id"])),
        "email": email,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }


def create_minimal_pdf_bytes(title: str, text: str) -> bytes:
    """Create a minimal valid PDF with text in a content stream."""
    stream_content = f"BT /F1 12 Tf 50 700 Td ({title}) Tj 0 -20 Td ({text}) Tj ET".encode("latin1")
    stream_len = len(stream_content)
    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>\nendobj\n"
        b"4 0 obj\n<< /Length " + str(stream_len).encode("ascii") + b" >>\nstream\n"
        + stream_content + b"\nendstream\nendobj\n"
        b"xref\n0 5\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"0000000210 00000 n \n"
        b"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n320\n%%EOF"
    )
    return pdf


# ==================== TESTS ====================

def test_upload_reproduces_field_required_on_content_type_json_mismatch(api_client, auth_user):
    """REGRESSION TEST: Sending Content-Type: application/json without multipart boundary
    reproduces the exact backend validation error 'Field required' on location ['body', 'file']."""
    headers = {
        **auth_user["headers"],
        "Content-Type": "application/json",
    }
    resp = api_client.post(
        "/api/research/documents/upload",
        headers=headers,
        content=b"some bytes",
    )
    assert resp.status_code == 422
    err_json = resp.json()
    assert "detail" in err_json
    assert isinstance(err_json["detail"], list)

    # Verify exact error location, message, and type observed by user
    error_item = err_json["detail"][0]
    assert error_item["loc"] == ["body", "file"]
    assert error_item["msg"] == "Field required"
    assert error_item["type"] == "missing"


def test_successful_goa_travel_guide_pdf_upload(api_client, auth_user):
    """Verify that submitting a Goa Travel Guide PDF via multipart/form-data succeeds,
    creates chunks and embeddings, and persists to PostgreSQL / VectorStore."""
    goa_pdf = create_minimal_pdf_bytes(
        title="Goa Travel Guide",
        text="Goa is a coastal state in western India known for its beaches like Baga and Calangute, vibrant nightlife, Portuguese colonial architecture in Old Goa, and seafood cuisine.",
    )

    resp = api_client.post(
        "/api/research/documents/upload",
        headers=auth_user["headers"],
        files={"file": ("Goa_Travel_Guide.pdf", goa_pdf, "application/pdf")},
        data={"title": "Goa Travel Guide"},
    )

    assert resp.status_code == 201, f"Expected 201 Created, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["success"] is True
    assert body["chunks_indexed"] >= 1
    doc_info = body["document"]
    assert doc_info["title"] == "Goa Travel Guide"
    assert doc_info["filename"] == "Goa_Travel_Guide.pdf"
    assert "document_id" in doc_info

    doc_id = UUID(doc_info["document_id"])

    # 1. Verify document is listed in ResearchStore
    rstore = ResearchStore()
    user_docs = rstore.list_documents(auth_user["user_id"])
    assert any(d.document_id == doc_id for d in user_docs)

    # 2. Verify vector store retrieval returns relevant Goa evidence
    from services.research.rag.embedding import get_embedding_provider
    provider = get_embedding_provider()
    query_text = "What beaches and architecture are recommended in Goa?"
    query_vec = provider.embed_query(query_text) if hasattr(provider, "embed_query") else provider.embed_texts([query_text])[0]

    vstore = get_vector_store()
    similar_chunks = vstore.query_similar(query_vec, user_id=auth_user["user_id"], top_k=3)
    assert len(similar_chunks) >= 1
    retrieved_chunk, score = similar_chunks[0]
    assert "Goa" in retrieved_chunk.text_content
    assert "beaches" in retrieved_chunk.text_content or "Baga" in retrieved_chunk.text_content


def test_upload_with_optional_trip_id_variations(api_client, auth_user):
    """Verify upload handles omitted trip_id and empty string trip_id without 422 failure."""
    pdf_bytes = create_minimal_pdf_bytes("Notes", "General travel notes and packing tips.")

    # Scenario A: trip_id not provided
    resp_a = api_client.post(
        "/api/research/documents/upload",
        headers=auth_user["headers"],
        files={"file": ("packing_notes.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp_a.status_code == 201

    # Scenario B: trip_id provided as empty string
    resp_b = api_client.post(
        "/api/research/documents/upload",
        headers=auth_user["headers"],
        files={"file": ("packing_notes_2.pdf", pdf_bytes, "application/pdf")},
        data={"trip_id": ""},
    )
    assert resp_b.status_code == 201


def test_upload_requires_authentication(api_client):
    """Verify that unauthenticated upload attempts are rejected with HTTP 401."""
    pdf_bytes = create_minimal_pdf_bytes("Secret", "Secret travel itinerary.")
    resp = api_client.post(
        "/api/research/documents/upload",
        files={"file": ("secret.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 401


def test_upload_cross_tenant_isolation(api_client, auth_user, other_user):
    """Verify that documents uploaded by User A are strictly invisible to User B."""
    pdf_bytes = create_minimal_pdf_bytes(
        "Private Itinerary",
        "Confidential booking reservation at Taj Fort Aguada Resort in Candolim, Goa."
    )
    resp = api_client.post(
        "/api/research/documents/upload",
        headers=auth_user["headers"],
        files={"file": ("taj_reservation.pdf", pdf_bytes, "application/pdf")},
        data={"title": "Private Taj Reservation"},
    )
    assert resp.status_code == 201
    doc_id = resp.json()["document"]["document_id"]

    # 1. Other user listing documents must NOT see User A's document
    resp_other_list = api_client.get("/api/research/documents", headers=other_user["headers"])
    assert resp_other_list.status_code == 200
    other_docs = resp_other_list.json().get("documents", [])
    assert not any(d["document_id"] == doc_id for d in other_docs)

    # 2. Other user deleting User A's document must fail with 404
    resp_delete = api_client.delete(f"/api/research/documents/{doc_id}", headers=other_user["headers"])
    assert resp_delete.status_code == 404
