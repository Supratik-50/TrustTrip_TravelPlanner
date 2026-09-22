"""Razorpay server-side payment adapter.

No card data is accepted by this service. Razorpay Checkout handles payment
collection in the browser; this service creates orders, verifies signatures,
and can initiate refunds.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

import requests
import time


class RazorpayError(RuntimeError):
    pass


class RazorpayService:
    BASE_URL = "https://api.razorpay.com/v1"

    def __init__(self) -> None:
        self.key_id = os.getenv("RAZORPAY_KEY_ID")
        self.key_secret = os.getenv("RAZORPAY_KEY_SECRET")
        self.webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
        self.timeout = int(os.getenv("RAZORPAY_API_TIMEOUT_SECONDS", "30"))

    @property
    def configured(self) -> bool:
        return bool(self.key_id and self.key_secret)

    def _request(self, method: str, path: str, *, retry_network: bool = False, **kwargs: Any) -> dict[str, Any]:
        if not self.configured:
            raise RazorpayError("Razorpay is not configured. Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET to .env.")
        attempts = 4 if retry_network and method.upper() in {"GET", "HEAD"} else 1
        last_exc = None
        for attempt in range(attempts):
            try:
                response = requests.request(
                    method,
                    f"{self.BASE_URL}{path}",
                    auth=(self.key_id, self.key_secret),
                    timeout=self.timeout,
                    **kwargs,
                )
                break
            except requests.RequestException as exc:
                last_exc = exc
                if attempt + 1 >= attempts:
                    raise RazorpayError(f"Razorpay request failed after {attempts} attempt(s): {exc}") from exc
                # DNS/connectivity can briefly fail on local Windows networks.
                # Retry only idempotent reads; never retry order creation.
                time.sleep(0.75 * (2 ** attempt))
        else:
            raise RazorpayError(f"Razorpay request failed: {last_exc}") from last_exc
        if not response.ok:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            raise RazorpayError(f"Razorpay HTTP {response.status_code}: {detail}")
        return response.json()

    def create_order(self, amount_rupees: float, receipt: str, notes: dict[str, str] | None = None) -> dict[str, Any]:
        amount_paise = int(round(amount_rupees * 100))
        if amount_paise <= 0:
            raise RazorpayError("Payment amount must be greater than zero.")
        return self._request(
            "POST", "/orders",
            json={
                "amount": amount_paise,
                "currency": "INR",
                "receipt": receipt[:40],
                "notes": notes or {},
            },
        )

    def get_order(self, order_id: str) -> dict[str, Any]:
        if not order_id:
            raise RazorpayError("Razorpay order ID is required.")
        return self._request("GET", f"/orders/{order_id}", retry_network=True)

    def verify_checkout_signature(self, order_id: str, payment_id: str, signature: str) -> bool:
        if not self.key_secret:
            raise RazorpayError("RAZORPAY_KEY_SECRET is missing.")
        digest = hmac.new(
            self.key_secret.encode(),
            f"{order_id}|{payment_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(digest, signature)

    def fetch_payment(self, payment_id: str) -> dict[str, Any]:
        return self._request("GET", f"/payments/{payment_id}", retry_network=True)

    def refund(self, payment_id: str, amount_rupees: float | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if amount_rupees is not None:
            payload["amount"] = int(round(amount_rupees * 100))
        headers = {"X-Refund-Idempotency": idempotency_key} if idempotency_key else {}
        return self._request("POST", f"/payments/{payment_id}/refund", json=payload, headers=headers)

    def verify_webhook(self, raw_body: bytes, signature: str) -> bool:
        if not self.webhook_secret:
            raise RazorpayError("RAZORPAY_WEBHOOK_SECRET is missing.")
        digest = hmac.new(self.webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(digest, signature)
