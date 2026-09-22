"""Tests for SearchSessionStore, Encryption, and Deduplication."""
from __future__ import annotations

import base64
import os
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from domain.live_offer import (
    FlightOffer,
    PriceBreakdown,
)
from services.search_session_store import (
    SearchSessionStore,
    _get_encryption_key,
    decrypt_payload,
    encrypt_payload,
)


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    SearchSessionStore._IN_MEMORY_SESSIONS.clear()
    SearchSessionStore._IN_MEMORY_SNAPSHOTS.clear()
    SearchSessionStore._IN_MEMORY_REVALIDATIONS.clear()


def test_encryption_key_must_be_32_bytes(monkeypatch):
    """Verify key validation enforces exactly 32 bytes (256-bit)."""
    # 16-byte key (too short)
    short_key = base64.b64encode(secrets.token_bytes(16)).decode("utf-8")
    monkeypatch.setenv("PAYLOAD_ENCRYPTION_KEY", short_key)
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "0")

    with pytest.raises(RuntimeError, match="must decode to exactly 32 bytes"):
        _get_encryption_key()

    # Valid 32-byte key
    valid_key = base64.b64encode(secrets.token_bytes(32)).decode("utf-8")
    monkeypatch.setenv("PAYLOAD_ENCRYPTION_KEY", valid_key)
    key_bytes = _get_encryption_key()
    assert len(key_bytes) == 32


def test_aes_gcm_encryption_generates_unique_nonce_and_authenticates():
    """Verify encryption generates distinct ciphertexts for identical plaintexts and detects tampering."""
    key = secrets.token_bytes(32)
    payload = {"flight": "AI101", "fare": 450.0}

    cipher1 = encrypt_payload(payload, key)
    cipher2 = encrypt_payload(payload, key)

    # Unique nonce guarantees different output blobs
    assert cipher1 != cipher2
    assert cipher1[:12] != cipher2[:12]  # Nonces are different

    # Decrypt restores identical data
    assert decrypt_payload(cipher1, key) == payload
    assert decrypt_payload(cipher2, key) == payload

    # Tampering with ciphertext raises error
    tampered = bytearray(cipher1)
    tampered[15] ^= 0xFF
    with pytest.raises(Exception):
        decrypt_payload(bytes(tampered), key)


def test_session_deduplication_via_criteria_hash():
    """Verify active session is reused when criteria_hash matches within session TTL."""
    user_id = uuid4()
    trip_id = uuid4()
    store = SearchSessionStore()

    session1 = store.create_session(
        user_id=user_id,
        trip_id=trip_id,
        thread_id="th_1",
        search_type="flights",
        criteria_hash="hash_identical",
        reuse_ttl_seconds=600,
    )

    # Lookup by criteria hash
    found = store.get_active_session_by_hash(user_id, trip_id, "hash_identical")
    assert found is not None
    assert found.session_id == session1.session_id

    # Lookup with different hash returns None
    assert store.get_active_session_by_hash(user_id, trip_id, "hash_different") is None
