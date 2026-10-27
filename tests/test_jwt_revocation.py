"""Focused test suite for Phase 3C: Server-Side JWT Revocation.

Verifies:
1. Newly issued tokens contain unique identifiers (jti claim).
2. Valid token accepted before revocation.
3. Revoked cookie token rejected (HTTP 401).
4. Revoked Bearer token rejected (HTTP 401).
5. Logout invalidates current access token (including previously copied Bearer tokens).
6. Duplicate/repeated logout succeeds idempotently (HTTP 200).
7. Expired revocation records cleanup without premature record deletion.
8. Revocation storage unavailable fails closed (HTTP 503 on required and optional auth).
9. Existing tokens without jti:
   - Accepted when unrevoked (backward compatibility).
   - Invalidate correctly upon logout (hash-based revocation).
10. Existing user ownership and tenant isolation preserved across revocations.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
import unittest
from unittest.mock import patch
import jwt

os.environ["USE_IN_MEMORY_AUTH"] = "1"
os.environ["JWT_SECRET"] = "phase-3c-jwt-revocation-test-secret-at-least-32-bytes-long"
os.environ["AUTH_SECRET"] = "phase-3c-jwt-revocation-test-secret-at-least-32-bytes-long"

from fastapi.testclient import TestClient
from app import app
from services.auth_store import RevocationStoreError, TokenRevocationStore, UserStore
from services.trip_store import TripStore
from services.auth_service import (
    JWT_ALGORITHM,
    create_access_token,
    decode_access_token,
    get_auth_secret,
)
from services.config import COOKIE_NAME


class JwtRevocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        UserStore._IN_MEMORY_USERS.clear()
        TripStore._IN_MEMORY_TRIPS.clear()
        TripStore._IN_MEMORY_THREADS.clear()
        TokenRevocationStore._IN_MEMORY_REVOKED.clear()

    # 1. Newly issued tokens contain unique jti
    def test_newly_issued_tokens_contain_unique_jti(self):
        token_a = create_access_token("user-1", "user1@trusttrip.com")
        token_b = create_access_token("user-1", "user1@trusttrip.com")
        
        payload_a = decode_access_token(token_a)
        payload_b = decode_access_token(token_b)

        self.assertIn("jti", payload_a)
        self.assertIn("jti", payload_b)
        self.assertTrue(len(payload_a["jti"]) > 10)
        self.assertNotEqual(payload_a["jti"], payload_b["jti"])

    # 2. Valid token accepted before revocation
    def test_valid_token_accepted_before_revocation(self):
        signup_res = self.client.post(
            "/api/auth/signup",
            json={"email": "valid_token@trusttrip.com", "password": "SecurePassword123!"},
        )
        self.assertEqual(signup_res.status_code, 201)
        token = signup_res.json()["access_token"]

        # Works via Bearer header
        bearer_res = self.client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(bearer_res.status_code, 200)
        self.assertEqual(bearer_res.json()["user"]["email"], "valid_token@trusttrip.com")

        # Works via Cookie
        cookie_res = self.client.get(
            "/api/auth/me",
            cookies={COOKIE_NAME: token},
        )
        self.assertEqual(cookie_res.status_code, 200)
        self.assertEqual(cookie_res.json()["user"]["email"], "valid_token@trusttrip.com")

    # 3. Revoked cookie token rejected (HTTP 401)
    def test_revoked_cookie_token_rejected(self):
        signup_res = self.client.post(
            "/api/auth/signup",
            headers={"X-Requested-With": "XMLHttpRequest"},
            json={"email": "cookie_revocation@trusttrip.com", "password": "SecurePassword123!"},
        )
        self.assertEqual(signup_res.status_code, 201)
        
        # Extract cookie from Set-Cookie header
        set_cookie = signup_res.headers.get("set-cookie", "")
        self.assertIn(f"{COOKIE_NAME}=", set_cookie)
        token = set_cookie.split(f"{COOKIE_NAME}=")[1].split(";")[0].strip()

        # Call logout with cookie and CSRF header
        logout_res = self.client.post(
            "/api/auth/logout",
            cookies={COOKIE_NAME: token},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(logout_res.status_code, 200)

        # Attacker/client attempts to present old cookie to /api/auth/me
        me_res = self.client.get(
            "/api/auth/me",
            cookies={COOKIE_NAME: token},
        )
        self.assertEqual(me_res.status_code, 401)
        self.assertIn("revoked", me_res.json()["detail"].lower())

    # 4. Revoked Bearer token rejected (HTTP 401)
    def test_revoked_bearer_token_rejected(self):
        signup_res = self.client.post(
            "/api/auth/signup",
            json={"email": "bearer_revocation@trusttrip.com", "password": "SecurePassword123!"},
        )
        token = signup_res.json()["access_token"]

        # Call logout with Bearer header
        logout_res = self.client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(logout_res.status_code, 200)

        # Present the revoked Bearer token
        me_res = self.client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(me_res.status_code, 401)
        self.assertIn("revoked", me_res.json()["detail"].lower())

    # 5. Logout invalidates current access token (including previously copied Bearer token)
    def test_logout_invalidates_previously_copied_bearer_token(self):
        signup_res = self.client.post(
            "/api/auth/signup",
            json={"email": "copied_token@trusttrip.com", "password": "SecurePassword123!"},
        )
        token = signup_res.json()["access_token"]

        # User is authenticated in browser via cookie and logs out
        logout_res = self.client.post(
            "/api/auth/logout",
            cookies={COOKIE_NAME: token},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(logout_res.status_code, 200)

        # Attacker tries using the copied raw token as Bearer authorization
        attacker_res = self.client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(attacker_res.status_code, 401)
        self.assertIn("revoked", attacker_res.json()["detail"].lower())

    # 6. Duplicate / repeated logout succeeds idempotently
    def test_duplicate_repeated_logout_succeeds_idempotently(self):
        signup_res = self.client.post(
            "/api/auth/signup",
            json={"email": "repeat_logout@trusttrip.com", "password": "SecurePassword123!"},
        )
        token = signup_res.json()["access_token"]

        # 1st logout
        res1 = self.client.post(
            "/api/auth/logout",
            cookies={COOKIE_NAME: token},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res1.status_code, 200)
        self.assertTrue(res1.json()["success"])

        # 2nd logout with same token
        res2 = self.client.post(
            "/api/auth/logout",
            cookies={COOKIE_NAME: token},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res2.status_code, 200)
        self.assertTrue(res2.json()["success"])

        # 3rd logout with no credentials
        res3 = self.client.post(
            "/api/auth/logout",
        )
        self.assertEqual(res3.status_code, 200)
        self.assertTrue(res3.json()["success"])

    # 7. Expired revocation records cleanup without premature record deletion
    def test_expired_revocation_records_and_cleanup(self):
        store = TokenRevocationStore()
        now = datetime.now(timezone.utc)

        # Record 1: Expired 1 hour ago
        store._memory_revoked["expired-token-jti"] = {
            "jti": "expired-token-jti",
            "user_id": "u1",
            "token_hash": "hash1",
            "revoked_at": now - timedelta(hours=2),
            "expires_at": now - timedelta(hours=1),
        }

        # Record 2: Active revocation (expires in 23 hours)
        store._memory_revoked["active-token-jti"] = {
            "jti": "active-token-jti",
            "user_id": "u2",
            "token_hash": "hash2",
            "revoked_at": now,
            "expires_at": now + timedelta(hours=23),
        }

        deleted_count = store.cleanup_expired_revocations()
        self.assertEqual(deleted_count, 1)
        self.assertNotIn("expired-token-jti", store._memory_revoked)
        self.assertIn("active-token-jti", store._memory_revoked)

    # 8. Revocation storage unavailable fails closed (HTTP 503)
    def test_revocation_storage_unavailable_fails_closed(self):
        signup_res = self.client.post(
            "/api/auth/signup",
            json={"email": "store_down@trusttrip.com", "password": "SecurePassword123!"},
        )
        token = signup_res.json()["access_token"]

        with patch.object(
            TokenRevocationStore,
            "is_token_revoked",
            side_effect=RevocationStoreError("Database connection lost"),
        ):
            # Required auth route raises 503
            me_res = self.client.get(
                "/api/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(me_res.status_code, 503)
            self.assertIn("unavailable", me_res.json()["detail"].lower())

            # Optional auth route (/api/travel/pdf) also fails closed with 503 (not anonymous 200)
            pdf_res = self.client.post(
                "/api/travel/pdf",
                headers={"Authorization": f"Bearer {token}"},
                json={"plan": {"trip_name": "Test", "origin": "A", "destination": "B", "departure_date": "2099-01-01", "return_date": "2099-01-08", "duration_days": 7, "travelers": 1, "budget_usd": 500, "currency": "USD", "daily_plan": [], "estimated_costs": {}, "total_estimated_cost_usd": 0, "booking_recommendations": [], "citations": []}},
            )
            self.assertEqual(pdf_res.status_code, 503)

    # 9. Existing tokens without jti: accepted when unrevoked
    def test_existing_tokens_without_jti_accepted_when_unrevoked(self):
        user_store = UserStore()
        user = user_store.create_user("legacy_user@trusttrip.com", "hash")
        
        # Forge a valid legacy JWT without jti claim
        expire = datetime.now(timezone.utc) + timedelta(hours=12)
        payload = {
            "sub": str(user["id"]),
            "email": "legacy_user@trusttrip.com",
            "exp": int(expire.timestamp()),
            "iat": int(datetime.now(timezone.utc).timestamp()),
        }
        legacy_token = jwt.encode(payload, get_auth_secret(), algorithm=JWT_ALGORITHM)

        # Must be accepted before revocation
        me_res = self.client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {legacy_token}"},
        )
        self.assertEqual(me_res.status_code, 200)
        self.assertEqual(me_res.json()["user"]["email"], "legacy_user@trusttrip.com")

    # 10. Existing tokens without jti: invalidated correctly upon logout (hash-based)
    def test_existing_tokens_without_jti_invalidated_upon_logout(self):
        user_store = UserStore()
        user = user_store.create_user("legacy_logout@trusttrip.com", "hash")
        
        expire = datetime.now(timezone.utc) + timedelta(hours=12)
        payload = {
            "sub": str(user["id"]),
            "email": "legacy_logout@trusttrip.com",
            "exp": int(expire.timestamp()),
            "iat": int(datetime.now(timezone.utc).timestamp()),
        }
        legacy_token = jwt.encode(payload, get_auth_secret(), algorithm=JWT_ALGORITHM)

        # Log out using the legacy token
        logout_res = self.client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {legacy_token}"},
        )
        self.assertEqual(logout_res.status_code, 200)

        # Present the revoked legacy token
        me_res = self.client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {legacy_token}"},
        )
        self.assertEqual(me_res.status_code, 401)
        self.assertIn("revoked", me_res.json()["detail"].lower())

    # 11. Existing user ownership and tenant isolation preserved across revocations
    def test_user_ownership_and_tenant_isolation_preserved(self):
        # User A
        res_a = self.client.post(
            "/api/auth/signup",
            json={"email": "alice_tenant@trusttrip.com", "password": "SecurePassword123!"},
        )
        token_a = res_a.json()["access_token"]

        # User B
        res_b = self.client.post(
            "/api/auth/signup",
            json={"email": "bob_tenant@trusttrip.com", "password": "SecurePassword123!"},
        )
        token_b = res_b.json()["access_token"]

        # Revoke User A's token
        self.client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {token_a}"},
        )

        # User A is revoked (401)
        me_a = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_a}"})
        self.assertEqual(me_a.status_code, 401)

        # User B's token remains completely active (200)
        me_b = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_b}"})
        self.assertEqual(me_b.status_code, 200)
        self.assertEqual(me_b.json()["user"]["email"], "bob_tenant@trusttrip.com")

    # 12. Cookie logout during revocation-store outage returns HTTP 503 with cookie-deletion Set-Cookie header
    def test_cookie_logout_outage_returns_503_and_deletes_cookie(self):
        signup_res = self.client.post(
            "/api/auth/signup",
            json={"email": "logout_outage@trusttrip.com", "password": "SecurePassword123!"},
        )
        self.assertEqual(signup_res.status_code, 201)
        set_cookie_signup = signup_res.headers.get("set-cookie", "")
        self.assertIn(f"{COOKIE_NAME}=", set_cookie_signup)
        token = set_cookie_signup.split(f"{COOKIE_NAME}=")[1].split(";")[0].strip()

        with patch.object(
            TokenRevocationStore,
            "revoke_token",
            side_effect=RevocationStoreError("Postgres revocation store offline"),
        ):
            # Attempt logout via cookie with CSRF header while store is down
            logout_res = self.client.post(
                "/api/auth/logout",
                cookies={COOKIE_NAME: token},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )

            # 1. Must return HTTP 503 Service Unavailable
            self.assertEqual(logout_res.status_code, 503)
            # 2. Must not claim the token was successfully revoked
            self.assertIn("unavailable", logout_res.json()["detail"].lower())

            # 3. Must include the cookie-deletion Set-Cookie header
            outage_set_cookie = logout_res.headers.get("set-cookie", "")
            self.assertIn(f"{COOKIE_NAME}=", outage_set_cookie)
            self.assertIn("max-age=0", outage_set_cookie.lower())
            self.assertIn("path=/api", outage_set_cookie.lower())
            self.assertIn("httponly", outage_set_cookie.lower())
            self.assertIn("samesite=lax", outage_set_cookie.lower())

        # 4. Token not successfully revoked remains subject to normal server-side validation:
        # 4a. While store remains offline, server-side auth fails closed with 503
        with patch.object(
            TokenRevocationStore,
            "is_token_revoked",
            side_effect=RevocationStoreError("Postgres revocation store offline"),
        ):
            me_down = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(me_down.status_code, 503)

        # 4b. When store recovers, token was NOT revoked on server, so it validates successfully
        me_recovered = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me_recovered.status_code, 200)
        self.assertEqual(me_recovered.json()["user"]["email"], "logout_outage@trusttrip.com")

        # 4c. Subsequent logout when store is healthy successfully revokes token
        second_logout = self.client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(second_logout.status_code, 200)

        # 4d. Token is now properly revoked (401)
        me_revoked = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me_revoked.status_code, 401)
        self.assertIn("revoked", me_revoked.json()["detail"].lower())

    # 13. Bearer logout during revocation-store outage returns HTTP 503 without clearing cookies
    def test_bearer_logout_outage_returns_503_without_cookie_deletion(self):
        signup_res = self.client.post(
            "/api/auth/signup",
            json={"email": "bearer_outage@trusttrip.com", "password": "SecurePassword123!"},
        )
        token = signup_res.json()["access_token"]

        with patch.object(
            TokenRevocationStore,
            "revoke_token",
            side_effect=RevocationStoreError("Postgres revocation store offline"),
        ):
            logout_res = self.client.post(
                "/api/auth/logout",
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(logout_res.status_code, 503)
            self.assertIn("unavailable", logout_res.json()["detail"].lower())
            # Bearer API logout without browser indicators must not emit Set-Cookie
            self.assertNotIn("set-cookie", logout_res.headers)

    # 14. Cookie logout during outage preserves CSRF enforcement
    def test_cookie_logout_outage_preserves_csrf_enforcement(self):
        signup_res = self.client.post(
            "/api/auth/signup",
            json={"email": "csrf_outage@trusttrip.com", "password": "SecurePassword123!"},
        )
        set_cookie = signup_res.headers.get("set-cookie", "")
        token = set_cookie.split(f"{COOKIE_NAME}=")[1].split(";")[0].strip()

        with patch.object(
            TokenRevocationStore,
            "revoke_token",
            side_effect=RevocationStoreError("Postgres revocation store offline"),
        ):
            # Missing CSRF headers -> rejected with 403 before revocation store is even touched
            logout_res = self.client.post(
                "/api/auth/logout",
                cookies={COOKIE_NAME: token},
            )
            self.assertEqual(logout_res.status_code, 403)
            self.assertIn("csrf", logout_res.json()["detail"].lower())


if __name__ == "__main__":
    unittest.main()

