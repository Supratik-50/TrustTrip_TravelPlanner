"""Focused test suite for Phase 2A: Backend HttpOnly Cookie Authentication Lifecycle.

Verifies:
1. Cookie issuance on successful signup and login.
2. Cookie attributes (HttpOnly=True, SameSite=Lax, Path=/api, Max-Age=86400, Secure).
3. Authentication using valid cookie (no Authorization header).
4. Bearer-token fallback compatibility (no cookie).
5. Conflicting cookie and Bearer credentials rejection (HTTP 401).
6. Missing, malformed, expired, and invalid-signature cookie rejection (HTTP 401).
7. Logout endpoint clears cookie with identical path and attributes.
8. CSRF protections:
   - State-changing cookie request rejected without custom CSRF header (HTTP 403).
   - State-changing cookie request accepted with X-Requested-With or X-CSRF-Token.
   - Disallowed Origin rejected on auth endpoints and cookie requests (HTTP 403).
   - Cross-site Sec-Fetch-Site rejected (HTTP 403).
9. Tenant isolation and ownership preservation under cookie auth.
10. Production configuration rejecting wildcard credentialed CORS origins.
"""
from __future__ import annotations

import os
from datetime import timedelta
import unittest
from unittest.mock import patch

os.environ["USE_IN_MEMORY_AUTH"] = "1"
os.environ["JWT_SECRET"] = "phase-2a-cookie-auth-lifecycle-test-secret-at-least-32-bytes-long"
os.environ["AUTH_SECRET"] = "phase-2a-cookie-auth-lifecycle-test-secret-at-least-32-bytes-long"

from fastapi.testclient import TestClient
from app import app
from services.auth_store import TokenRevocationStore, UserStore
from services.trip_store import TripStore
from services.auth_service import create_access_token, get_auth_secret
from services.config import COOKIE_NAME, validate_config
import jwt


class CookieAuthLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        UserStore._IN_MEMORY_USERS.clear()
        TripStore._IN_MEMORY_TRIPS.clear()
        TripStore._IN_MEMORY_THREADS.clear()
        TokenRevocationStore._IN_MEMORY_REVOKED.clear()

    def _parse_cookie_attributes(self, set_cookie_str: str) -> dict[str, str | bool]:
        """Parse attributes from a Set-Cookie header string."""
        parts = [p.strip() for p in set_cookie_str.split(";")]
        main = parts[0]
        key, val = main.split("=", 1) if "=" in main else (main, "")
        attrs: dict[str, str | bool] = {"name": key, "value": val}
        for attr in parts[1:]:
            if "=" in attr:
                k, v = attr.split("=", 1)
                attrs[k.strip().lower()] = v.strip()
            else:
                attrs[attr.strip().lower()] = True
        return attrs

    def _get_cookie_header(self, response, cookie_name: str = COOKIE_NAME) -> str | None:
        """Find the Set-Cookie header for cookie_name."""
        for header in response.headers.get_list("set-cookie"):
            if header.startswith(f"{cookie_name}="):
                return header
        return None

    # 1. Cookie issuance on successful signup
    def test_cookie_issued_on_signup(self):
        res = self.client.post(
            "/api/auth/signup",
            json={"email": "cookie_signup@trusttrip.com", "password": "SecurePassword123!"},
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertIn("access_token", data)
        self.assertEqual(data["user"]["email"], "cookie_signup@trusttrip.com")

        # Verify Set-Cookie header
        raw_cookie = self._get_cookie_header(res)
        self.assertIsNotNone(raw_cookie, "Set-Cookie header was not returned on signup")
        attrs = self._parse_cookie_attributes(raw_cookie)
        self.assertEqual(attrs["name"], COOKIE_NAME)
        self.assertEqual(attrs["value"], data["access_token"])
        self.assertTrue(attrs.get("httponly"))
        self.assertEqual(attrs.get("samesite", "").lower(), "lax")
        self.assertEqual(attrs.get("path"), "/api")
        self.assertEqual(attrs.get("max-age"), "86400")

    # 2. Cookie issuance on successful login
    def test_cookie_issued_on_login(self):
        # Create user
        self.client.post(
            "/api/auth/signup",
            json={"email": "cookie_login@trusttrip.com", "password": "SecurePassword123!"},
        )
        # Login
        res = self.client.post(
            "/api/auth/login",
            json={"email": "cookie_login@trusttrip.com", "password": "SecurePassword123!"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("access_token", data)

        raw_cookie = self._get_cookie_header(res)
        self.assertIsNotNone(raw_cookie, "Set-Cookie header was not returned on login")
        attrs = self._parse_cookie_attributes(raw_cookie)
        self.assertEqual(attrs["name"], COOKIE_NAME)
        self.assertEqual(attrs["value"], data["access_token"])
        self.assertTrue(attrs.get("httponly"))
        self.assertEqual(attrs.get("samesite", "").lower(), "lax")
        self.assertEqual(attrs.get("path"), "/api")
        self.assertEqual(attrs.get("max-age"), "86400")

    # 3. Secure flag behavior in production vs development
    def test_secure_cookie_flag_configuration(self):
        self.client.post(
            "/api/auth/signup",
            json={"email": "cookie_secure@trusttrip.com", "password": "SecurePassword123!"},
        )

        # In dev with SECURE_COOKIES=1
        with patch.dict(os.environ, {"SECURE_COOKIES": "1", "APP_ENV": "development"}):
            res = self.client.post(
                "/api/auth/login",
                json={"email": "cookie_secure@trusttrip.com", "password": "SecurePassword123!"},
            )
            raw = self._get_cookie_header(res)
            self.assertIsNotNone(raw)
            attrs = self._parse_cookie_attributes(raw)
            self.assertTrue(attrs.get("secure"), "Expected Secure=True when SECURE_COOKIES=1")

        # In dev with SECURE_COOKIES=0 / unset
        with patch.dict(os.environ, {"SECURE_COOKIES": "0", "APP_ENV": "development"}):
            res = self.client.post(
                "/api/auth/login",
                json={"email": "cookie_secure@trusttrip.com", "password": "SecurePassword123!"},
            )
            raw = self._get_cookie_header(res)
            self.assertIsNotNone(raw)
            attrs = self._parse_cookie_attributes(raw)
            self.assertFalse(attrs.get("secure", False), "Expected Secure=False in dev without SECURE_COOKIES")

        # In production APP_ENV=production, Secure is always enforced
        with patch.dict(os.environ, {"APP_ENV": "production", "SECURE_COOKIES": "0"}):
            res = self.client.post(
                "/api/auth/login",
                json={"email": "cookie_secure@trusttrip.com", "password": "SecurePassword123!"},
            )
            raw = self._get_cookie_header(res)
            self.assertIsNotNone(raw)
            attrs = self._parse_cookie_attributes(raw)
            self.assertTrue(attrs.get("secure"), "Expected Secure=True in production regardless of SECURE_COOKIES")

    # 4. Authentication using a valid cookie
    def test_auth_with_valid_cookie(self):
        signup_res = self.client.post(
            "/api/auth/signup",
            json={"email": "cookie_auth@trusttrip.com", "password": "SecurePassword123!"},
        )
        token = signup_res.json()["access_token"]
        user_id = signup_res.json()["user"]["id"]

        # Call /api/auth/me using ONLY cookie, no Authorization header
        me_res = self.client.get(
            "/api/auth/me",
            cookies={COOKIE_NAME: token},
        )
        self.assertEqual(me_res.status_code, 200)
        body = me_res.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["user"]["id"], user_id)
        self.assertEqual(body["user"]["email"], "cookie_auth@trusttrip.com")

    # 5. Bearer-token fallback compatibility
    def test_auth_with_bearer_token_fallback(self):
        signup_res = self.client.post(
            "/api/auth/signup",
            json={"email": "bearer_auth@trusttrip.com", "password": "SecurePassword123!"},
        )
        token = signup_res.json()["access_token"]
        user_id = signup_res.json()["user"]["id"]

        # Call /api/auth/me using ONLY Authorization: Bearer, no cookies
        me_res = self.client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(me_res.status_code, 200)
        self.assertEqual(me_res.json()["user"]["id"], user_id)

    # 6. Identical cookie and Bearer credentials accepted
    def test_identical_cookie_and_bearer_credentials_accepted(self):
        signup_res = self.client.post(
            "/api/auth/signup",
            json={"email": "dual_auth@trusttrip.com", "password": "SecurePassword123!"},
        )
        token = signup_res.json()["access_token"]

        me_res = self.client.get(
            "/api/auth/me",
            cookies={COOKIE_NAME: token},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(me_res.status_code, 200)
        self.assertEqual(me_res.json()["user"]["email"], "dual_auth@trusttrip.com")

    # 7. Conflicting cookie and Bearer credentials rejected
    def test_conflicting_cookie_and_bearer_credentials_rejected(self):
        res_a = self.client.post(
            "/api/auth/signup",
            json={"email": "user_a@trusttrip.com", "password": "SecurePassword123!"},
        )
        token_a = res_a.json()["access_token"]

        res_b = self.client.post(
            "/api/auth/signup",
            json={"email": "user_b@trusttrip.com", "password": "SecurePassword123!"},
        )
        token_b = res_b.json()["access_token"]

        # Pass User A cookie and User B Bearer in a browser context
        res = self.client.get(
            "/api/auth/me",
            cookies={COOKIE_NAME: token_a},
            headers={
                "Authorization": f"Bearer {token_b}",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        self.assertEqual(res.status_code, 401)
        self.assertIn("conflicting", res.json()["detail"].lower())

    # 8. Missing credentials
    def test_missing_credentials_returns_401(self):
        res = self.client.get("/api/auth/me")
        self.assertEqual(res.status_code, 401)
        self.assertIn("not provided", res.json()["detail"].lower())

    # 9. Malformed token cookie
    def test_malformed_cookie_returns_401(self):
        res = self.client.get(
            "/api/auth/me",
            cookies={COOKIE_NAME: "not-a-valid-jwt-token"},
        )
        self.assertEqual(res.status_code, 401)
        self.assertIn("could not validate", res.json()["detail"].lower())

    # 10. Expired cookie token
    def test_expired_cookie_returns_401(self):
        user_store = UserStore()
        user = user_store.create_user(email="expired@trusttrip.com", password_hash="hash")
        expired_token = create_access_token(
            user["id"],
            user["email"],
            expires_delta=timedelta(seconds=-60),
        )

        res = self.client.get(
            "/api/auth/me",
            cookies={COOKIE_NAME: expired_token},
        )
        self.assertEqual(res.status_code, 401)
        self.assertIn("expired", res.json()["detail"].lower())

    # 11. Invalid signature cookie token
    def test_invalid_signature_cookie_returns_401(self):
        user_store = UserStore()
        user = user_store.create_user(email="tampered@trusttrip.com", password_hash="hash")
        foreign_secret = "foreign-signing-secret-key-with-sufficient-length-32-bytes"
        payload = {"sub": str(user["id"]), "email": user["email"], "exp": 9999999999, "iat": 1000}
        foreign_token = jwt.encode(payload, foreign_secret, algorithm="HS256")

        res = self.client.get(
            "/api/auth/me",
            cookies={COOKIE_NAME: foreign_token},
        )
        self.assertEqual(res.status_code, 401)
        self.assertIn("could not validate", res.json()["detail"].lower())

    # 12. Deactivated user with valid cookie
    def test_deactivated_user_cookie_returns_403(self):
        user_store = UserStore()
        user = user_store.create_user(email="deactivated@trusttrip.com", password_hash="hash")
        token = create_access_token(user["id"], user["email"])

        # Deactivate user
        UserStore._IN_MEMORY_USERS[str(user["id"])]["is_active"] = False

        res = self.client.get(
            "/api/auth/me",
            cookies={COOKIE_NAME: token},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("deactivated", res.json()["detail"].lower())

    # 13. Logout clears cookie
    def test_logout_deletes_cookie(self):
        signup_res = self.client.post(
            "/api/auth/signup",
            json={"email": "logout_user@trusttrip.com", "password": "SecurePassword123!"},
        )
        token = signup_res.json()["access_token"]

        # Call logout with cookie and CSRF header
        logout_res = self.client.post(
            "/api/auth/logout",
            cookies={COOKIE_NAME: token},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(logout_res.status_code, 200)
        self.assertTrue(logout_res.json()["success"])

        # Inspect Set-Cookie header for deletion (Max-Age=0 or expired)
        raw_cookie = self._get_cookie_header(logout_res)
        self.assertIsNotNone(raw_cookie, "Set-Cookie header was not returned on logout")
        attrs = self._parse_cookie_attributes(raw_cookie)
        self.assertEqual(attrs["name"], COOKIE_NAME)
        self.assertEqual(attrs.get("path"), "/api")
        self.assertTrue(attrs.get("httponly"))
        # Deletion cookie should have Max-Age=0 or value=""
        self.assertTrue(attrs.get("max-age") == "0" or attrs.get("value") == '""' or attrs.get("value") == "")

    # 14. CSRF rejection: cookie-authenticated state-changing POST without CSRF header
    def test_csrf_rejection_missing_header_on_cookie_post(self):
        signup_res = self.client.post(
            "/api/auth/signup",
            json={"email": "csrf_test@trusttrip.com", "password": "SecurePassword123!"},
        )
        token = signup_res.json()["access_token"]

        # Attempt POST /api/trips with cookie but NO custom CSRF header
        res = self.client.post(
            "/api/trips",
            cookies={COOKIE_NAME: token},
            json={"title": "Unauthorized CSRF Trip"},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("csrf", res.json()["detail"].lower())

    # 15. CSRF acceptance: cookie-authenticated state-changing POST with X-Requested-With
    def test_csrf_acceptance_with_x_requested_with(self):
        signup_res = self.client.post(
            "/api/auth/signup",
            json={"email": "csrf_pass1@trusttrip.com", "password": "SecurePassword123!"},
        )
        token = signup_res.json()["access_token"]

        res = self.client.post(
            "/api/trips",
            cookies={COOKIE_NAME: token},
            headers={"X-Requested-With": "XMLHttpRequest"},
            json={"title": "Authorized Cookie Trip"},
        )
        self.assertEqual(res.status_code, 201)
        self.assertTrue(res.json()["success"])
        self.assertEqual(res.json()["trip"]["title"], "Authorized Cookie Trip")

    # 16. CSRF acceptance: cookie-authenticated state-changing POST with X-CSRF-Token
    def test_csrf_acceptance_with_x_csrf_token(self):
        signup_res = self.client.post(
            "/api/auth/signup",
            json={"email": "csrf_pass2@trusttrip.com", "password": "SecurePassword123!"},
        )
        token = signup_res.json()["access_token"]

        res = self.client.post(
            "/api/trips",
            cookies={COOKIE_NAME: token},
            headers={"X-CSRF-Token": "test-client-token"},
            json={"title": "Authorized Token Trip"},
        )
        self.assertEqual(res.status_code, 201)
        self.assertTrue(res.json()["success"])

    # 17. CSRF rejection: disallowed Origin on cookie-authenticated request
    def test_csrf_rejection_disallowed_origin(self):
        signup_res = self.client.post(
            "/api/auth/signup",
            json={"email": "csrf_origin@trusttrip.com", "password": "SecurePassword123!"},
        )
        token = signup_res.json()["access_token"]

        # Call with malicious cross-site origin
        res = self.client.post(
            "/api/trips",
            cookies={COOKIE_NAME: token},
            headers={
                "Origin": "http://malicious-attacker.com",
                "X-Requested-With": "XMLHttpRequest",
            },
            json={"title": "Attacker Injected Trip"},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("origin not allowed", res.json()["detail"].lower())

    # 18. CSRF rejection: cross-site Sec-Fetch-Site on cookie request
    def test_csrf_rejection_cross_site_sec_fetch_site(self):
        signup_res = self.client.post(
            "/api/auth/signup",
            json={"email": "csrf_fetch@trusttrip.com", "password": "SecurePassword123!"},
        )
        token = signup_res.json()["access_token"]

        res = self.client.post(
            "/api/trips",
            cookies={COOKIE_NAME: token},
            headers={
                "Sec-Fetch-Site": "cross-site",
                "X-Requested-With": "XMLHttpRequest",
            },
            json={"title": "Cross Site Trip"},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("cross-site", res.json()["detail"].lower())

    # 19. CSRF rejection on auth login/signup endpoints with untrusted origin
    def test_auth_login_signup_csrf_origin_validation(self):
        # Signup with untrusted Origin
        res_signup = self.client.post(
            "/api/auth/signup",
            headers={"Origin": "http://evil-tracker.com"},
            json={"email": "evil_signup@trusttrip.com", "password": "Password123!"},
        )
        self.assertEqual(res_signup.status_code, 403)
        self.assertIn("origin not allowed", res_signup.json()["detail"].lower())

        # Login with untrusted Origin
        res_login = self.client.post(
            "/api/auth/login",
            headers={"Origin": "http://evil-tracker.com"},
            json={"email": "any@trusttrip.com", "password": "Password123!"},
        )
        self.assertEqual(res_login.status_code, 403)
        self.assertIn("origin not allowed", res_login.json()["detail"].lower())

    # 20. Logout CSRF protection: cookie present without custom header rejected
    def test_logout_csrf_rejection_when_cookie_present_without_header(self):
        signup_res = self.client.post(
            "/api/auth/signup",
            json={"email": "logout_csrf@trusttrip.com", "password": "SecurePassword123!"},
        )
        token = signup_res.json()["access_token"]

        # Call logout with cookie but WITHOUT custom CSRF header
        res = self.client.post(
            "/api/auth/logout",
            cookies={COOKIE_NAME: token},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("csrf", res.json()["detail"].lower())

    # 21. Multi-tenant isolation preserved under cookie authentication
    def test_tenant_isolation_under_cookie_authentication(self):
        res_a = self.client.post(
            "/api/auth/signup",
            json={"email": "tenant_a@trusttrip.com", "password": "SecurePassword123!"},
        )
        token_a = res_a.json()["access_token"]
        user_a_id = res_a.json()["user"]["id"]

        res_b = self.client.post(
            "/api/auth/signup",
            json={"email": "tenant_b@trusttrip.com", "password": "SecurePassword123!"},
        )
        token_b = res_b.json()["access_token"]

        # User A creates a trip using cookie authentication
        trip_res = self.client.post(
            "/api/trips",
            cookies={COOKIE_NAME: token_a},
            headers={"X-Requested-With": "XMLHttpRequest"},
            json={"title": "User A Private Trip"},
        )
        self.assertEqual(trip_res.status_code, 201)
        trip_id = trip_res.json()["trip"]["id"]
        self.assertEqual(str(trip_res.json()["trip"]["user_id"]), user_a_id)

        # User B attempts to access User A's trip using User B's cookie
        access_res = self.client.get(
            f"/api/trips/{trip_id}",
            cookies={COOKIE_NAME: token_b},
        )
        self.assertEqual(access_res.status_code, 404)

        # User B attempts to delete User A's trip using User B's cookie
        del_res = self.client.delete(
            f"/api/trips/{trip_id}",
            cookies={COOKIE_NAME: token_b},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(del_res.status_code, 404)

        # User A can still retrieve the trip
        verify_res = self.client.get(
            f"/api/trips/{trip_id}",
            cookies={COOKIE_NAME: token_a},
        )
        self.assertEqual(verify_res.status_code, 200)

    # 22. Production configuration rejects wildcard credentialed CORS
    def test_production_rejects_wildcard_credentialed_cors(self):
        # In production with ALLOWED_ORIGINS=* -> validate_config must fail
        with patch.dict(os.environ, {"APP_ENV": "production", "ALLOWED_ORIGINS": "*"}):
            with self.assertRaises(RuntimeError) as ctx:
                validate_config()
            self.assertIn("wildcard '*' in allowed_origins is prohibited in production", str(ctx.exception).lower())

        # In production with explicit origins -> validate_config passes
        with patch.dict(os.environ, {"APP_ENV": "production", "ALLOWED_ORIGINS": "https://app.trusttrip.com"}):
            try:
                validate_config()
            except RuntimeError as exc:
                self.fail(f"validate_config raised unexpectedly for explicit origin: {exc}")


if __name__ == "__main__":
    unittest.main()


class OptionalAuthCsrfRegressionTests(unittest.TestCase):
    """Phase 3D regression tests: get_optional_current_user must not swallow HTTP 403.

    Before the fix, get_optional_current_user caught all HTTPException and returned
    None, silently converting CSRF failures (HTTP 403) into anonymous-user access.
    After the fix, HTTP 403 exceptions are re-raised while HTTP 401 continues to
    produce None (unauthenticated / optional-auth semantics preserved).

    Covered scenarios:
    A. CSRF rejection propagated through optional-auth route (/api/travel/pdf POST).
    B. Disallowed-origin rejection propagated through optional-auth route.
    C. Cross-site Sec-Fetch-Site rejection propagated through optional-auth route.
    D. Deactivated-account 403 propagated (not silently treated as anonymous).
    E. Invalid credentials (401) still produce anonymous access on optional-auth route.
    F. No credentials (anonymous) still allowed on optional-auth route.
    G. Valid Bearer token works on optional-auth route (no cookie, no CSRF required).
    H. Valid cookie with correct CSRF header works on optional-auth route.
    """

    # A minimal valid plan payload accepted by /api/travel/pdf without a trip_id
    _PLAN_PAYLOAD = {
        "plan": {
            "trip_name": "Regression Test Trip",
            "origin": "London",
            "destination": "Paris",
            "departure_date": "2099-06-01",
            "return_date": "2099-06-08",
            "duration_days": 7,
            "travelers": 2,
            "budget_usd": 1000,
            "currency": "USD",
            "metadata": {},
            "daily_plan": [
                {"day": i + 1, "title": f"Day {i + 1}", "activities": [], "accommodation": {}}
                for i in range(7)
            ],
            "estimated_costs": {},
            "total_estimated_cost_usd": 0,
            "booking_recommendations": [],
            "citations": [],
        }
    }

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=True)

    def setUp(self):
        UserStore._IN_MEMORY_USERS.clear()
        TripStore._IN_MEMORY_TRIPS.clear()
        TripStore._IN_MEMORY_THREADS.clear()
        TokenRevocationStore._IN_MEMORY_REVOKED.clear()

    def _signup_and_token(self, email: str, password: str = "SecurePassword123!") -> str:
        res = self.client.post(
            "/api/auth/signup",
            json={"email": email, "password": password},
        )
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()["access_token"]

    # ------------------------------------------------------------------ #
    # A. CSRF 403 propagated on optional-auth POST (missing custom header) #
    # ------------------------------------------------------------------ #
    def test_optional_auth_csrf_rejection_missing_header_returns_403(self):
        """Cookie-authenticated POST to optional-auth route without CSRF header → 403, not 401."""
        token = self._signup_and_token("opt_csrf_a@trusttrip.com")

        res = self.client.post(
            "/api/travel/pdf",
            cookies={COOKIE_NAME: token},
            # No X-Requested-With / X-CSRF-Token header
            json=self._PLAN_PAYLOAD,
        )
        self.assertEqual(res.status_code, 403, res.text)
        self.assertIn("csrf", res.json()["detail"].lower())

    # ------------------------------------------------------------------ #
    # B. Disallowed origin 403 propagated on optional-auth route          #
    # ------------------------------------------------------------------ #
    def test_optional_auth_csrf_rejection_disallowed_origin_returns_403(self):
        """Cookie-authenticated POST with disallowed Origin → 403, not anonymous None."""
        token = self._signup_and_token("opt_csrf_b@trusttrip.com")

        res = self.client.post(
            "/api/travel/pdf",
            cookies={COOKIE_NAME: token},
            headers={
                "Origin": "http://evil-attacker.example.com",
                "X-Requested-With": "XMLHttpRequest",
            },
            json=self._PLAN_PAYLOAD,
        )
        self.assertEqual(res.status_code, 403, res.text)
        self.assertIn("origin not allowed", res.json()["detail"].lower())

    # ------------------------------------------------------------------ #
    # C. Cross-site Sec-Fetch-Site 403 propagated on optional-auth route  #
    # ------------------------------------------------------------------ #
    def test_optional_auth_csrf_rejection_cross_site_sec_fetch_returns_403(self):
        """Cookie-authenticated POST with Sec-Fetch-Site: cross-site → 403, not anonymous None."""
        token = self._signup_and_token("opt_csrf_c@trusttrip.com")

        res = self.client.post(
            "/api/travel/pdf",
            cookies={COOKIE_NAME: token},
            headers={
                "Sec-Fetch-Site": "cross-site",
                "X-Requested-With": "XMLHttpRequest",
            },
            json=self._PLAN_PAYLOAD,
        )
        self.assertEqual(res.status_code, 403, res.text)
        self.assertIn("cross-site", res.json()["detail"].lower())

    # ------------------------------------------------------------------ #
    # D. Deactivated-account 403 propagated on optional-auth route        #
    # ------------------------------------------------------------------ #
    def test_optional_auth_deactivated_account_returns_403_not_anonymous(self):
        """Deactivated account with valid cookie on optional-auth route → 403, not anonymous."""
        user_store = UserStore()
        user = user_store.create_user(
            email="opt_deactivated@trusttrip.com", password_hash="hash"
        )
        token = create_access_token(user["id"], user["email"])
        UserStore._IN_MEMORY_USERS[str(user["id"])]["is_active"] = False

        # Use GET /api/auth/me which is required-auth, but the deactivated-user
        # 403 is raised by get_current_user; we verify optional-auth surfaces it.
        # We test via a direct call through the FastAPI TestClient using a
        # minimal route that uses get_optional_current_user: /api/travel/pdf
        # with Bearer so no CSRF is triggered (only deactivation check matters).
        res = self.client.post(
            "/api/travel/pdf",
            headers={"Authorization": f"Bearer {token}"},
            json=self._PLAN_PAYLOAD,
        )
        self.assertEqual(res.status_code, 403, res.text)
        self.assertIn("deactivated", res.json()["detail"].lower())

    # ------------------------------------------------------------------ #
    # E. Invalid credentials (401) → anonymous access on optional-auth    #
    # ------------------------------------------------------------------ #
    def test_optional_auth_invalid_token_treated_as_anonymous(self):
        """Malformed Bearer token on optional-auth route → treated as no-auth (plan accepted)."""
        res = self.client.post(
            "/api/travel/pdf",
            headers={"Authorization": "Bearer this.is.not.a.valid.jwt"},
            json=self._PLAN_PAYLOAD,
        )
        # The route accepts anonymous callers when a plan payload is provided.
        # A malformed token is a 401 from get_current_user; optional-auth must
        # return None so the route proceeds as anonymous (not 401).
        self.assertNotEqual(res.status_code, 401, res.text)

    # ------------------------------------------------------------------ #
    # F. No credentials → anonymous access allowed on optional-auth route #
    # ------------------------------------------------------------------ #
    def test_optional_auth_no_credentials_allowed_on_optional_route(self):
        """Completely unauthenticated POST to optional-auth route with plan payload → accepted."""
        res = self.client.post(
            "/api/travel/pdf",
            json=self._PLAN_PAYLOAD,
        )
        # Should not be 401 or 403 — anonymous callers can generate PDFs.
        self.assertNotIn(res.status_code, (401, 403), res.text)

    # ------------------------------------------------------------------ #
    # G. Valid Bearer token works (no cookie, no CSRF required)           #
    # ------------------------------------------------------------------ #
    def test_optional_auth_valid_bearer_token_accepted_without_csrf(self):
        """Bearer-authenticated POST to optional-auth route → accepted without CSRF header."""
        token = self._signup_and_token("opt_bearer@trusttrip.com")

        res = self.client.post(
            "/api/travel/pdf",
            headers={"Authorization": f"Bearer {token}"},
            json=self._PLAN_PAYLOAD,
        )
        # CSRF is only checked for cookie-authenticated requests.
        self.assertNotIn(res.status_code, (401, 403), res.text)

    # ------------------------------------------------------------------ #
    # H. Cookie + valid CSRF header → accepted on optional-auth route     #
    # ------------------------------------------------------------------ #
    def test_optional_auth_cookie_with_csrf_header_accepted(self):
        """Cookie-authenticated POST with X-Requested-With → accepted on optional-auth route."""
        token = self._signup_and_token("opt_cookie_csrf@trusttrip.com")

        res = self.client.post(
            "/api/travel/pdf",
            cookies={COOKIE_NAME: token},
            headers={"X-Requested-With": "XMLHttpRequest"},
            json=self._PLAN_PAYLOAD,
        )
        self.assertNotIn(res.status_code, (401, 403), res.text)


# ================================================================== #
# Phase 3A: Browser-Safe Authentication Responses (No JWT in JSON)    #
# ================================================================== #

class BrowserSafeAuthResponseTests(CookieAuthLifecycleTests):
    """Phase 3A: Browser-Safe Authentication Responses.

    Verifies:
    1. Browser signup/login omits raw access_token from JSON while issuing HttpOnly cookie.
    2. Explicit X-Auth-Mode: cookie and ?response_mode=cookie trigger browser response mode.
    3. Legacy non-browser API clients continue receiving access_token in JSON.
    4. Explicit X-Auth-Mode: token overrides browser headers to return access_token in JSON.
    """

    def test_browser_signup_omits_access_token_in_json(self):
        """Browser signup with X-Requested-With sets cookie and omits raw access_token from JSON."""
        res = self.client.post(
            "/api/auth/signup",
            headers={"X-Requested-With": "XMLHttpRequest"},
            json={"email": "browser_signup_3a@trusttrip.com", "password": "SecurePassword123!"},
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertIsNone(data.get("access_token"), "Expected access_token to be None in browser response mode")
        self.assertIsNone(data.get("token_type"), "Expected token_type to be None in browser response mode")
        self.assertEqual(data["user"]["email"], "browser_signup_3a@trusttrip.com")

        # Verify HttpOnly cookie was set
        raw_cookie = self._get_cookie_header(res)
        self.assertIsNotNone(raw_cookie)
        attrs = self._parse_cookie_attributes(raw_cookie)
        self.assertEqual(attrs["name"], COOKIE_NAME)
        self.assertTrue(attrs.get("httponly"))
        self.assertTrue(len(str(attrs["value"])) > 20)

        # Verify cookie works immediately for authenticated calls
        me_res = self.client.get("/api/auth/me", cookies={COOKIE_NAME: attrs["value"]})
        self.assertEqual(me_res.status_code, 200)
        self.assertEqual(me_res.json()["user"]["email"], "browser_signup_3a@trusttrip.com")

    def test_browser_login_omits_access_token_in_json(self):
        """Browser login with X-Requested-With sets cookie and omits raw access_token from JSON."""
        # Create user via legacy signup
        self.client.post(
            "/api/auth/signup",
            json={"email": "browser_login_3a@trusttrip.com", "password": "SecurePassword123!"},
        )

        res = self.client.post(
            "/api/auth/login",
            headers={"X-Requested-With": "XMLHttpRequest"},
            json={"email": "browser_login_3a@trusttrip.com", "password": "SecurePassword123!"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIsNone(data.get("access_token"), "Expected access_token to be None in browser login mode")
        self.assertIsNone(data.get("token_type"), "Expected token_type to be None in browser login mode")
        self.assertEqual(data["user"]["email"], "browser_login_3a@trusttrip.com")

        # Verify cookie was set
        raw_cookie = self._get_cookie_header(res)
        self.assertIsNotNone(raw_cookie)
        attrs = self._parse_cookie_attributes(raw_cookie)
        self.assertEqual(attrs["name"], COOKIE_NAME)
        self.assertTrue(attrs.get("httponly"))

    def test_explicit_auth_mode_cookie_header(self):
        """Explicit X-Auth-Mode: cookie triggers browser-safe response (no token in JSON)."""
        self.client.post(
            "/api/auth/signup",
            json={"email": "explicit_mode_cookie@trusttrip.com", "password": "SecurePassword123!"},
        )

        res = self.client.post(
            "/api/auth/login",
            headers={"X-Auth-Mode": "cookie"},
            json={"email": "explicit_mode_cookie@trusttrip.com", "password": "SecurePassword123!"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIsNone(data.get("access_token"))
        self.assertIsNotNone(self._get_cookie_header(res))

    def test_explicit_response_mode_cookie_query_param(self):
        """Query parameter ?response_mode=cookie triggers browser-safe response."""
        self.client.post(
            "/api/auth/signup",
            json={"email": "query_mode_cookie@trusttrip.com", "password": "SecurePassword123!"},
        )

        res = self.client.post(
            "/api/auth/login?response_mode=cookie",
            json={"email": "query_mode_cookie@trusttrip.com", "password": "SecurePassword123!"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIsNone(data.get("access_token"))
        self.assertIsNotNone(self._get_cookie_header(res))

    def test_legacy_bearer_client_still_receives_access_token(self):
        """Standard API client without browser headers receives access_token in JSON for backward compatibility."""
        res = self.client.post(
            "/api/auth/signup",
            json={"email": "legacy_compat@trusttrip.com", "password": "SecurePassword123!"},
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertIsNotNone(data.get("access_token"))
        self.assertEqual(data.get("token_type"), "bearer")
        self.assertTrue(len(data["access_token"]) > 20)

        # Login also returns access_token for legacy clients
        login_res = self.client.post(
            "/api/auth/login",
            json={"email": "legacy_compat@trusttrip.com", "password": "SecurePassword123!"},
        )
        self.assertEqual(login_res.status_code, 200)
        login_data = login_res.json()
        self.assertIsNotNone(login_data.get("access_token"))
        self.assertEqual(login_data.get("token_type"), "bearer")

        # Bearer token is functional
        me_res = self.client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {login_data['access_token']}"},
        )
        self.assertEqual(me_res.status_code, 200)
        self.assertEqual(me_res.json()["user"]["email"], "legacy_compat@trusttrip.com")

    def test_explicit_auth_mode_token_overrides_browser_headers(self):
        """Explicit X-Auth-Mode: token overrides browser header to return access_token in JSON."""
        self.client.post(
            "/api/auth/signup",
            json={"email": "override_to_token@trusttrip.com", "password": "SecurePassword123!"},
        )

        res = self.client.post(
            "/api/auth/login",
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "X-Auth-Mode": "token",
            },
            json={"email": "override_to_token@trusttrip.com", "password": "SecurePassword123!"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIsNotNone(data.get("access_token"), "Expected access_token when explicitly requested")
        self.assertEqual(data.get("token_type"), "bearer")

