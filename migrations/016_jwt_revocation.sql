-- Migration: 016_jwt_revocation.sql
-- Description: Persistent server-side JWT revocation storage for explicit logout and token invalidation.

CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti TEXT PRIMARY KEY,
    user_id TEXT NULL,
    token_hash TEXT NULL,
    revoked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires_at ON revoked_tokens (expires_at);
CREATE INDEX IF NOT EXISTS idx_revoked_tokens_token_hash ON revoked_tokens (token_hash);
