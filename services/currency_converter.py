"""Server-side currency conversion for customer-facing INR pricing.

Uses Frankfurter's public latest-rate endpoint (no API key) as a daily
reference rate. The exact rate used for a payment is stored with the
transaction so later booking/refund operations do not silently use a new rate.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import requests


class CurrencyConversionError(RuntimeError):
    pass


class CurrencyConverter:
    BASE_URL = "https://api.frankfurter.dev/v2"

    def __init__(self, timeout: int = 10) -> None:
        self.timeout = timeout

    def get_rate(self, base: str, quote: str) -> dict[str, Any]:
        base = str(base or "").upper().strip()
        quote = str(quote or "").upper().strip()
        if not base or not quote or len(base) != 3 or len(quote) != 3:
            raise CurrencyConversionError("Invalid currency code for conversion.")
        if base == quote:
            return {"date": None, "base": base, "quote": quote, "rate": Decimal("1")}
        try:
            response = requests.get(
                f"{self.BASE_URL}/rate/{base}/{quote}",
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json(parse_float=Decimal)
            rate = Decimal(str(data.get("rate")))
            if rate <= 0:
                raise ValueError("Non-positive exchange rate")
            return {
                "date": data.get("date"),
                "base": str(data.get("base") or base).upper(),
                "quote": str(data.get("quote") or quote).upper(),
                "rate": rate,
            }
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            raise CurrencyConversionError(
                f"Unable to obtain the latest {base}/{quote} exchange rate."
            ) from exc

    def convert(self, amount: float | Decimal, base: str, quote: str) -> dict[str, Any]:
        source_amount = Decimal(str(amount))
        if source_amount <= 0:
            raise CurrencyConversionError("Amount must be greater than zero.")
        rate_data = self.get_rate(base, quote)
        converted = (source_amount * rate_data["rate"]).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        return {
            "source_amount": source_amount,
            "source_currency": rate_data["base"],
            "target_amount": converted,
            "target_currency": rate_data["quote"],
            "rate": rate_data["rate"],
            "rate_date": rate_data["date"],
            "source": "Frankfurter latest reference rate",
        }
