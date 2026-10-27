"""Centralized configuration validation for TrustTrip backend (Phase 10).

Validates required and environment-specific variables at application startup.
Follows a plain-Python, zero-enterprise approach without pydantic-settings.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Required in production; dev falls back with warning
PRODUCTION_SECURITY_VARS = ["JWT_SECRET", "PAYLOAD_ENCRYPTION_KEY"]

# Optional external integration credential groups (service degrades gracefully)
OPTIONAL_INTEGRATION_VARS: list[tuple[str, list[str]]] = [
    ("Duffel Flight API", ["DUFFEL_TEST_API_TOKEN", "DUFFEL_LIVE_API_TOKEN", "DUFFEL_API_TOKEN", "DUFFEL_ACCESS_TOKEN"]),
    ("Razorpay Payments", ["RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"]),
    ("LLM Intelligence", ["GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"]),
    ("Search / Weather", ["TAVILY_API_KEY", "OPENWEATHER_API_KEY"]),
    ("Hotel Providers", ["LITEAPI_SANDBOX_API_KEY", "LITEAPI_PRODUCTION_API_KEY", "HOTELBEDS_API_KEY"]),
]


def is_in_memory_auth() -> bool:
    """Return True if running in in-memory test mode."""
    return os.getenv("USE_IN_MEMORY_AUTH", "").lower() in {"1", "true", "yes"}


def is_production() -> bool:
    """Return True if running in production mode."""
    return os.getenv("APP_ENV", "development").lower() == "production"


def get_jwt_secret() -> str | None:
    """Return configured JWT secret or None if unset."""
    return os.getenv("JWT_SECRET") or os.getenv("AUTH_SECRET") or os.getenv("AUTH_SECRET_KEY")


COOKIE_NAME = "trusttrip_access_token"
DEFAULT_COOKIE_MAX_AGE = 86400  # 24 hours in seconds
DEFAULT_COOKIE_PATH = "/api"
DEFAULT_COOKIE_SAMESITE = "lax"


def get_allowed_origins() -> list[str]:
    """Return configured list of trusted origins for CORS and CSRF validation."""
    raw_origins = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost,http://127.0.0.1,http://localhost:3000,http://localhost:8000",
    )
    origins = [o.strip() for o in raw_origins.split(",") if o.strip()]
    frontend_env = os.getenv("FRONTEND_API_URL") or os.getenv("FRONTEND_URL")
    if frontend_env and frontend_env.strip() and frontend_env.strip() not in origins:
        origins.append(frontend_env.strip())
    return origins


def get_secure_cookie_setting() -> bool:
    """Return True if cookies should be sent with Secure attribute.

    In production (APP_ENV=production), always True.
    In development/testing, configurable via SECURE_COOKIES env var (defaults to False).
    """
    if is_production():
        return True
    val = os.getenv("SECURE_COOKIES", "").lower()
    return val in {"1", "true", "yes"}


def validate_config() -> None:
    """Validate environment configuration at application startup.

    Hard fails (RuntimeError):
      - DATABASE_URL missing when USE_IN_MEMORY_AUTH is not enabled.
      - In production (APP_ENV=production): JWT_SECRET or PAYLOAD_ENCRYPTION_KEY missing.

    Soft warnings:
      - In development: missing JWT_SECRET or PAYLOAD_ENCRYPTION_KEY (safe dev fallbacks used).
      - Missing optional provider credentials (related features degrade gracefully).
    """
    # 1. DATABASE_URL validation
    if not is_in_memory_auth():
        db_url = os.getenv("DATABASE_URL")
        if not db_url or not db_url.strip():
            raise RuntimeError(
                "DATABASE_URL is not configured. A PostgreSQL connection string is required "
                "for persistent operation (or set USE_IN_MEMORY_AUTH=1 for test mode)."
            )

    # Ensure libpq connect_timeout is set if not already present
    if "PGCONNECT_TIMEOUT" not in os.environ:
        os.environ["PGCONNECT_TIMEOUT"] = os.getenv("POSTGRES_CONNECT_TIMEOUT", "5")

    # 2. Security credentials validation
    jwt_secret = get_jwt_secret()
    payload_key = os.getenv("PAYLOAD_ENCRYPTION_KEY")

    if is_production():
        if not jwt_secret:
            raise RuntimeError(
                "JWT_SECRET is required in production environment (APP_ENV=production)."
            )
        if not payload_key:
            raise RuntimeError(
                "PAYLOAD_ENCRYPTION_KEY is required in production environment (APP_ENV=production)."
            )
        if len(jwt_secret.encode("utf-8")) < 32:
            raise RuntimeError(
                "JWT_SECRET must be at least 32 bytes long in production environment (APP_ENV=production)."
            )
    else:
        if not jwt_secret:
            logger.warning(
                "JWT_SECRET is not set; an ephemeral secret will be used for this process. "
                "Set JWT_SECRET to keep user sessions across restarts."
            )
        elif len(jwt_secret.encode("utf-8")) < 32:
            logger.warning(
                "JWT_SECRET is shorter than 32 bytes (256 bits). "
                "A secret of at least 32 bytes is strongly recommended for HS256."
            )
        if not payload_key and not is_in_memory_auth():
            logger.warning(
                "PAYLOAD_ENCRYPTION_KEY is not set; an ephemeral AES key will be used. "
                "Set PAYLOAD_ENCRYPTION_KEY for persistent search session encryption."
            )

    # 3. Optional integration credentials (warn only)
    for group_name, env_names in OPTIONAL_INTEGRATION_VARS:
        if not any(os.getenv(k) for k in env_names):
            logger.warning(
                "Optional integration '%s' credentials not configured (%s). Features will degrade gracefully.",
                group_name,
                ", ".join(env_names),
            )

    # 4. CORS and credentialed origins validation
    allowed_origins = get_allowed_origins()
    if is_production():
        if "*" in allowed_origins:
            raise RuntimeError(
                "Wildcard '*' in ALLOWED_ORIGINS is prohibited in production when credentialed "
                "authentication is enabled."
            )

