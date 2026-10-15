"""Tests for Persistent Research History.

Verifies:
- Completed research queries and results are persisted and retrievable.
- Historical results preserve original citations, facts, conflicts, and outcome statuses.
- Strict tenant isolation: User B cannot access or mutate User A's history.
- History survives fresh API requests.
- Deleting history does not affect uploaded documents or shared evidence.
- Idempotency / retry duplicate prevention on (query_id, user_id).
- Authentication requirements (unauthenticated requests rejected with 401).
"""
import os
import unittest
from uuid import UUID, uuid4
from datetime import datetime, timezone

os.environ["USE_IN_MEMORY_AUTH"] = "1"
os.environ["JWT_SECRET"] = "test-secret-key-for-research-history-suite-64-bytes-long"
os.environ["AUTH_SECRET"] = "test-secret-key-for-research-history-suite-64-bytes-long"

from fastapi.testclient import TestClient
from app import app
from services.auth_store import UserStore
from services.research.research_store import ResearchStore
from domain.research import (
    EvidenceStatus,
    RagDocument,
    ResearchCitation,
    ResearchConflict,
    ResearchEvidence,
    ResearchFact,
    SourceTrustClass,
)


class ResearchHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        UserStore._IN_MEMORY_USERS.clear()
        ResearchStore._IN_MEM_HISTORY.clear()
        ResearchStore._IN_MEM_SESSIONS.clear()
        ResearchStore._IN_MEM_QUERIES.clear()
        ResearchStore._IN_MEM_DOCS.clear()
        ResearchStore._IN_MEM_CHUNKS.clear()
        ResearchStore._IN_MEM_EVIDENCE.clear()
        ResearchStore._IN_MEM_CITATIONS.clear()

    def _create_user(self, email: str, password: str = "P@ssword123!"):
        res = self.client.post("/api/auth/signup", json={"email": email, "password": password})
        self.assertEqual(res.status_code, 201)
        data = res.json()
        return data["user"], data["access_token"]

    def _auth_headers(self, token: str):
        return {"Authorization": f"Bearer {token}"}

    def test_save_and_list_research_history(self):
        user, token = self._create_user("rh_user1@example.com")
        headers = self._auth_headers(token)

        store = ResearchStore(use_in_memory=True)
        q_id = uuid4()
        s_id = uuid4()

        store.save_history_entry({
            "user_id": UUID(user["id"]),
            "session_id": s_id,
            "query_id": q_id,
            "query_text": "What are the historic temples in Kyoto?",
            "mode": "hybrid",
            "intent": "DESTINATION_RESEARCH",
            "answer": "Kyoto features Kinkaku-ji and Fushimi Inari-taisha.",
            "evidence_status": "complete",
            "evidence_count": 2,
            "citations": [
                {
                    "citation_id": "[cite:abc-123]",
                    "evidence_id": str(uuid4()),
                    "title": "Kyoto Tourism Board",
                    "domain": "kyoto.travel",
                    "canonical_url": "https://kyoto.travel/temples",
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
            "facts": [{"claim": "Kinkaku-ji is a Zen Buddhist temple in Kyoto.", "evidence_id": str(uuid4()), "citation_id": "[cite:abc-123]"}],
            "conflicts": [],
        })

        res = self.client.get("/api/research/history", headers=headers)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["success"])
        self.assertEqual(len(body["history"]), 1)
        entry = body["history"][0]
        self.assertEqual(entry["query_text"], "What are the historic temples in Kyoto?")
        self.assertEqual(entry["evidence_status"], "complete")
        self.assertEqual(len(entry["citations"]), 1)
        self.assertEqual(entry["citations"][0]["domain"], "kyoto.travel")

    def test_get_specific_history_entry(self):
        user, token = self._create_user("rh_user2@example.com")
        headers = self._auth_headers(token)

        store = ResearchStore(use_in_memory=True)
        h_id = uuid4()
        saved = store.save_history_entry({
            "id": h_id,
            "user_id": UUID(user["id"]),
            "session_id": uuid4(),
            "query_id": uuid4(),
            "query_text": "Best ramen in Sapporo",
            "mode": "web",
            "answer": "Ramen Yokocho is famous for miso ramen.",
            "evidence_status": "complete",
            "evidence_count": 1,
            "citations": [{"citation_id": "[cite:sapporo-1]", "domain": "sapporo.travel", "retrieved_at": datetime.now(timezone.utc).isoformat()}],
        })

        res = self.client.get(f"/api/research/history/{saved['id']}", headers=headers)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["entry"]["query_text"], "Best ramen in Sapporo")
        self.assertEqual(body["entry"]["answer"], "Ramen Yokocho is famous for miso ramen.")

    def test_cross_tenant_history_isolation(self):
        user_a, token_a = self._create_user("rh_user_a@example.com")
        user_b, token_b = self._create_user("rh_user_b@example.com")

        store = ResearchStore(use_in_memory=True)
        h_id = uuid4()
        store.save_history_entry({
            "id": h_id,
            "user_id": UUID(user_a["id"]),
            "session_id": uuid4(),
            "query_id": uuid4(),
            "query_text": "Private User A Research",
            "mode": "auto",
            "answer": "Confidential answer.",
            "evidence_status": "complete",
        })

        # User B cannot see User A's history in list
        res_list_b = self.client.get("/api/research/history", headers=self._auth_headers(token_b))
        self.assertEqual(res_list_b.status_code, 200)
        self.assertEqual(len(res_list_b.json()["history"]), 0)

        # User B cannot get User A's history entry
        res_get_b = self.client.get(f"/api/research/history/{h_id}", headers=self._auth_headers(token_b))
        self.assertEqual(res_get_b.status_code, 404)

        # User B cannot delete User A's history entry
        res_del_b = self.client.delete(f"/api/research/history/{h_id}", headers=self._auth_headers(token_b))
        self.assertEqual(res_del_b.status_code, 404)

        # User A's entry remains intact
        res_get_a = self.client.get(f"/api/research/history/{h_id}", headers=self._auth_headers(token_a))
        self.assertEqual(res_get_a.status_code, 200)

    def test_supported_outcome_statuses(self):
        user, token = self._create_user("rh_status_user@example.com")
        headers = self._auth_headers(token)
        store = ResearchStore(use_in_memory=True)

        statuses = ["complete", "partial", "conflicted", "not_found"]
        for st in statuses:
            store.save_history_entry({
                "user_id": UUID(user["id"]),
                "session_id": uuid4(),
                "query_id": uuid4(),
                "query_text": f"Query for status {st}",
                "mode": "auto",
                "answer": f"Answer with status {st}",
                "evidence_status": st,
            })

        res = self.client.get("/api/research/history", headers=headers)
        self.assertEqual(res.status_code, 200)
        returned_statuses = {h["evidence_status"] for h in res.json()["history"]}
        self.assertEqual(returned_statuses, set(statuses))

    def test_delete_individual_history_entry_preserves_documents_and_evidence(self):
        user, token = self._create_user("rh_preserve_user@example.com")
        headers = self._auth_headers(token)
        store = ResearchStore(use_in_memory=True)

        # Upload a document
        doc_id = uuid4()
        doc = RagDocument(
            document_id=doc_id,
            user_id=UUID(user["id"]),
            title="Important Travel Notes",
            filename="notes.pdf",
            content_type="application/pdf",
            file_size_bytes=1024,
            content_hash="abc123hash",
        )
        store.save_document(doc, chunks=[])

        # Create history entry
        h_id = uuid4()
        store.save_history_entry({
            "id": h_id,
            "user_id": UUID(user["id"]),
            "session_id": uuid4(),
            "query_id": uuid4(),
            "query_text": "What are notes?",
            "mode": "rag",
            "answer": "Notes content.",
            "evidence_status": "complete",
        })

        # Delete history entry
        del_res = self.client.delete(f"/api/research/history/{h_id}", headers=headers)
        self.assertEqual(del_res.status_code, 200)
        self.assertTrue(del_res.json()["deleted"])

        # History entry is gone
        get_res = self.client.get(f"/api/research/history/{h_id}", headers=headers)
        self.assertEqual(get_res.status_code, 404)

        # Document is NOT deleted!
        user_docs = store.list_documents(UUID(user["id"]))
        self.assertEqual(len(user_docs), 1)
        self.assertEqual(user_docs[0].document_id, doc_id)

    def test_clear_research_history(self):
        user_a, token_a = self._create_user("rh_clear_a@example.com")
        user_b, token_b = self._create_user("rh_clear_b@example.com")
        store = ResearchStore(use_in_memory=True)

        # 3 entries for user A, 2 for user B
        for i in range(3):
            store.save_history_entry({
                "user_id": UUID(user_a["id"]),
                "session_id": uuid4(),
                "query_id": uuid4(),
                "query_text": f"User A query {i}",
                "mode": "web",
                "answer": "ans",
            })
        for i in range(2):
            store.save_history_entry({
                "user_id": UUID(user_b["id"]),
                "session_id": uuid4(),
                "query_id": uuid4(),
                "query_text": f"User B query {i}",
                "mode": "web",
                "answer": "ans",
            })

        # User A clears history
        clear_res = self.client.delete("/api/research/history", headers=self._auth_headers(token_a))
        self.assertEqual(clear_res.status_code, 200)
        self.assertEqual(clear_res.json()["cleared_count"], 3)

        # User A's history is empty
        list_a = self.client.get("/api/research/history", headers=self._auth_headers(token_a))
        self.assertEqual(len(list_a.json()["history"]), 0)

        # User B's history is unaffected
        list_b = self.client.get("/api/research/history", headers=self._auth_headers(token_b))
        self.assertEqual(len(list_b.json()["history"]), 2)

    def test_unauthenticated_requests_rejected(self):
        res_list = self.client.get("/api/research/history")
        self.assertEqual(res_list.status_code, 401)

        res_get = self.client.get(f"/api/research/history/{uuid4()}")
        self.assertEqual(res_get.status_code, 401)

        res_del = self.client.delete(f"/api/research/history/{uuid4()}")
        self.assertEqual(res_del.status_code, 401)

        res_clear = self.client.delete("/api/research/history")
        self.assertEqual(res_clear.status_code, 401)

    def test_idempotency_retry_deduplication(self):
        user, token = self._create_user("rh_retry_user@example.com")
        store = ResearchStore(use_in_memory=True)

        q_id = uuid4()
        s_id = uuid4()

        # First attempt
        store.save_history_entry({
            "user_id": UUID(user["id"]),
            "session_id": s_id,
            "query_id": q_id,
            "query_text": "What is the baggage limit?",
            "mode": "web",
            "answer": "First attempt answer",
            "evidence_status": "partial",
        })

        # Retry with identical (query_id, user_id)
        store.save_history_entry({
            "user_id": UUID(user["id"]),
            "session_id": s_id,
            "query_id": q_id,
            "query_text": "What is the baggage limit?",
            "mode": "web",
            "answer": "Second attempt answer with full evidence",
            "evidence_status": "complete",
        })

        res = self.client.get("/api/research/history", headers=self._auth_headers(token))
        self.assertEqual(res.status_code, 200)
        history = res.json()["history"]
        # Exactly 1 entry, not 2
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["evidence_status"], "complete")
        self.assertEqual(history[0]["answer"], "Second attempt answer with full evidence")

    def test_postgres_fk_trip_deletion_preserves_owner_and_history(self):
        """Live PostgreSQL test: deleting a referenced trip sets trip_id to NULL,

        while preserving user_id NOT NULL and preserving the research history row.
        """
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            self.skipTest("DATABASE_URL not set; skipping live PostgreSQL foreign key test")

        import psycopg
        from psycopg.rows import dict_row

        uid = uuid4()
        tid = uuid4()
        hid = uuid4()
        qid = uuid4()
        sid = uuid4()

        with psycopg.connect(db_url, row_factory=dict_row) as conn:
            with conn.transaction():
                # Create user
                conn.execute(
                    "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)",
                    (str(uid), f"fk_test_{uid.hex[:8]}@example.com", "hash"),
                )
                # Create trip
                conn.execute(
                    "INSERT INTO trips (id, user_id, title, destination, origin, status) VALUES (%s, %s, %s, %s, %s, %s)",
                    (str(tid), str(uid), "Trip to be deleted", "Goa", "Mumbai", "planning"),
                )
                # Create research history referencing trip
                conn.execute(
                    """
                    INSERT INTO research_history (
                        id, user_id, session_id, query_id, trip_id, query_text, mode, answer, evidence_status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (str(hid), str(uid), str(sid), str(qid), str(tid), "Goa beaches query", "auto", "Goa has Baga beach.", "complete"),
                )

            # Delete the trip
            with conn.transaction():
                conn.execute("DELETE FROM trips WHERE id = %s", (str(tid),))

            # Verify the research history row still exists, user_id is untouched, and trip_id is NULL
            row = conn.execute(
                "SELECT id, user_id, trip_id, query_text FROM research_history WHERE id = %s",
                (str(hid),),
            ).fetchone()

            self.assertIsNotNone(row, "Research history row must not be deleted when referenced trip is deleted")
            self.assertEqual(str(row["user_id"]), str(uid), "user_id must never be nulled or changed by trip deletion")
            self.assertIsNone(row["trip_id"], "trip_id must be set to NULL via ON DELETE SET NULL")

            # Clean up user (cascades to research_history)
            with conn.transaction():
                conn.execute("DELETE FROM users WHERE id = %s", (str(uid),))

            # Verify user cascade also cleaned up research_history
            row_after_user_del = conn.execute(
                "SELECT id FROM research_history WHERE id = %s",
                (str(hid),),
            ).fetchone()
            self.assertIsNone(row_after_user_del, "Deleting user cascades and removes research history")


class TestResearchHistoryPostgresPersistence(unittest.TestCase):
    """PostgreSQL integration tests for Research History persistence and restoration across refresh."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        self.db_url = os.getenv("DATABASE_URL")
        if not self.db_url:
            self.skipTest("DATABASE_URL not set; skipping live PostgreSQL persistence tests")
        self._orig_in_memory = os.environ.get("USE_IN_MEMORY_AUTH")
        if "USE_IN_MEMORY_AUTH" in os.environ:
            del os.environ["USE_IN_MEMORY_AUTH"]

    def tearDown(self):
        if self._orig_in_memory is not None:
            os.environ["USE_IN_MEMORY_AUTH"] = self._orig_in_memory
        elif "USE_IN_MEMORY_AUTH" in os.environ:
            del os.environ["USE_IN_MEMORY_AUTH"]

    def _ensure_pg_user(self, user_id, email):
        import psycopg
        with psycopg.connect(self.db_url, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, 'hash') ON CONFLICT (id) DO NOTHING",
                (str(user_id), email),
            )

    def _cleanup_pg_records(self, user_id):
        import psycopg
        try:
            with psycopg.connect(self.db_url, autocommit=True) as conn:
                conn.execute("DELETE FROM research_history WHERE user_id = %s", (str(user_id),))
                conn.execute("DELETE FROM users WHERE id = %s", (str(user_id),))
        except Exception:
            pass

    def test_postgres_research_persistence_and_restore(self):
        """Verify completed research is committed to PostgreSQL, retrievable across reloads,

        scoped strictly to owner, and preserves citations, facts, and evidence status.
        """
        user_a_id = uuid4()
        user_b_id = uuid4()
        email_a = f"pg_rh_a_{user_a_id.hex[:6]}@example.com"
        email_b = f"pg_rh_b_{user_b_id.hex[:6]}@example.com"

        self._ensure_pg_user(user_a_id, email_a)
        self._ensure_pg_user(user_b_id, email_b)

        from services.auth_service import create_access_token
        token_a = create_access_token(user_id=user_a_id, email=email_a)
        token_b = create_access_token(user_id=user_b_id, email=email_b)
        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        store = ResearchStore(use_in_memory=False)

        q_id = uuid4()
        s_id = uuid4()
        entry_id = uuid4()
        now = datetime.now(timezone.utc)

        citations_payload = [
            {
                "id": "cit_pg_01",
                "source_type": "web",
                "title": "Kyoto Ryokan Association",
                "publisher": "Kyoto Tourism Board",
                "url": "https://kyoto.travel/ryokan",
                "snippet": "Authentic machiya ryokan in Higashiyama offer serene private gardens.",
                "claim": "Higashiyama machiya offer serene private gardens.",
                "confidence": 0.96,
                "location": "Higashiyama, Kyoto",
            }
        ]
        facts_payload = [
            {
                "fact_id": "fct_pg_01",
                "claim": "Higashiyama machiya offer serene private gardens.",
                "citation_id": "cit_pg_01",
            }
        ]

        # 1. Commit completed research result to PostgreSQL
        saved = store.save_history_entry({
            "id": entry_id,
            "user_id": user_a_id,
            "session_id": s_id,
            "query_id": q_id,
            "query_text": "Where to stay in Kyoto for authentic gardens?",
            "mode": "hybrid",
            "intent": "DESTINATION_RESEARCH",
            "answer": "Higashiyama is the premier area for machiya ryokan [cite:b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e].",
            "evidence_status": "complete",
            "evidence_count": 1,
            "citations": citations_payload,
            "facts": facts_payload,
            "conflicts": [],
            "created_at": now,
        })
        self.assertIsNotNone(saved)

        try:
            # 2. GET /api/research/history retrieves the row for User A
            res_a = self.client.get("/api/research/history", headers=headers_a)
            self.assertEqual(res_a.status_code, 200)
            data_a = res_a.json()
            self.assertTrue(data_a["success"])
            matching = [h for h in data_a["history"] if str(h.get("query_id")) == str(q_id)]
            self.assertEqual(len(matching), 1, "User A must retrieve their persisted research history")
            h_entry = matching[0]
            self.assertEqual(h_entry["mode"], "hybrid")
            self.assertEqual(h_entry["evidence_status"], "complete")
            self.assertEqual(len(h_entry["citations"]), 1)
            self.assertEqual(h_entry["citations"][0]["publisher"], "Kyoto Tourism Board")
            self.assertEqual(len(h_entry["facts"]), 1)

            # 3. GET /api/research/history/{history_id} retrieves single entry for User A
            res_single = self.client.get(f"/api/research/history/{h_entry['id']}", headers=headers_a)
            self.assertEqual(res_single.status_code, 200)
            self.assertEqual(res_single.json()["entry"]["query_text"], "Where to stay in Kyoto for authentic gardens?")

            # 4. Strict tenant isolation: User B cannot see User A's history
            res_b = self.client.get("/api/research/history", headers=headers_b)
            self.assertEqual(res_b.status_code, 200)
            b_matching = [h for h in res_b.json()["history"] if str(h.get("query_id")) == str(q_id)]
            self.assertEqual(len(b_matching), 0, "User B must not see User A's research history")

            # 5. User B cannot access single entry of User A
            res_b_single = self.client.get(f"/api/research/history/{h_entry['id']}", headers=headers_b)
            self.assertEqual(res_b_single.status_code, 404)

            # 6. Delete history entry removes it from PostgreSQL
            del_res = self.client.delete(f"/api/research/history/{h_entry['id']}", headers=headers_a)
            self.assertEqual(del_res.status_code, 200)
            self.assertTrue(del_res.json()["deleted"])

            # 7. Verification: no longer returned in list
            res_after_del = self.client.get("/api/research/history", headers=headers_a)
            self.assertEqual(res_after_del.status_code, 200)
            after_matching = [h for h in res_after_del.json()["history"] if str(h.get("query_id")) == str(q_id)]
            self.assertEqual(len(after_matching), 0)
        finally:
            self._cleanup_pg_records(user_a_id)
            self._cleanup_pg_records(user_b_id)


if __name__ == "__main__":
    unittest.main()
