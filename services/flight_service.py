"""Application service for flight search, ranking, repricing and booking."""
from __future__ import annotations

import os
from typing import Any

from .duffel_provider import DuffelProvider
from .flight_provider import FlightProvider, FlightProviderError
from .currency_converter import CurrencyConverter, CurrencyConversionError


class FlightService:
    def __init__(self, provider: FlightProvider | None = None):
        self.provider = provider or self._build_provider()
        self._converter = CurrencyConverter()
        self._rate_cache: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _build_provider() -> FlightProvider:
        # Duffel is intentionally the single flight provider. This keeps chat
        # recommendations and manual search on exactly the same bookable data
        # source and avoids falling back to informational-only flight APIs.
        provider_name = os.getenv("FLIGHT_PROVIDER", "duffel").lower().strip()
        if provider_name != "duffel":
            raise FlightProviderError(
                "Trust Trip flight search uses Duffel only. Set FLIGHT_PROVIDER=duffel."
            )
        return DuffelProvider()

    def _add_inr_prices(self, offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Attach INR display values without changing the provider source price."""
        for offer in offers:
            amount = offer.get("price")
            currency = str(offer.get("currency") or "").upper()
            if amount is None or not currency:
                continue
            if currency == "INR":
                offer["price_inr"] = round(float(amount), 2)
                offer["price_inr_source"] = "provider price (INR)"
                offer["exchange_rate"] = 1.0
                offer["exchange_rate_date"] = None
            else:
                try:
                    rate_data = self._rate_cache.get(currency)
                    if rate_data is None:
                        rate_data = self._converter.get_rate(currency, "INR")
                        self._rate_cache[currency] = rate_data
                    offer["price_inr"] = round(float(amount) * float(rate_data["rate"]), 2)
                    offer["price_inr_source"] = "reference FX conversion"
                    offer["exchange_rate"] = float(rate_data["rate"])
                    offer["exchange_rate_date"] = rate_data.get("date")
                except (CurrencyConversionError, ValueError, TypeError, KeyError):
                    offer["price_inr"] = None
                    offer["price_inr_source"] = "INR conversion unavailable"
                    offer["exchange_rate"] = None
                    offer["exchange_rate_date"] = None

            count = max(1, int(offer.get("passenger_count") or len(offer.get("passengers") or []) or 1))
            offer["price_per_traveller_inr"] = (
                round(float(offer["price_inr"]) / count, 2)
                if offer.get("price_inr") is not None else None
            )
        return offers

    @staticmethod
    def rank(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        clean = [o for o in offers if o.get("price") is not None]
        if not clean:
            return offers
        # Rank using INR when available so USD and INR offers are comparable.
        prices = [float(o.get("price_inr") if o.get("price_inr") is not None else o["price"]) for o in clean]
        min_price = min(prices)
        max_price = max(prices) or min_price

        for offer in clean:
            comparable_price = float(offer.get("price_inr") if offer.get("price_inr") is not None else offer["price"])
            price_score = 1 - ((comparable_price - min_price) / max(1, max_price - min_price))
            stop_score = 1 / (1 + offer.get("stops", 0))
            offer["recommendation_score"] = round(0.7 * price_score + 0.3 * stop_score, 4)

        return sorted(offers, key=lambda x: x.get("recommendation_score", -1), reverse=True)

    def search(self, request: dict[str, Any]) -> dict[str, Any]:
        offers = self.provider.search(request)
        offers = self._add_inr_prices(offers)
        ranked = self.rank(offers)
        return {
            "provider": self.provider.name,
            "count": len(ranked),
            "offers": ranked,
            "bookable": self.provider.name == "duffel",
        }

    def recheck(self, offer_id: str) -> dict[str, Any]:
        return self.provider.get_offer(offer_id)

    def book_after_recheck(
        self,
        offer_id: str,
        expected_amount: float | None,
        passengers: list[dict[str, Any]],
        expected_currency: str | None = None,
        payment: dict[str, Any] | None = None,
        allow_price_change: bool = False,
        payment_mode: str = "auto",
    ) -> dict[str, Any]:
        latest = self.recheck(offer_id)
        current_amount = latest.get("price")
        current_currency = str(latest.get("currency") or "").upper()
        if current_amount is None:
            raise FlightProviderError("The selected offer has no current price.")

        currency_changed = bool(expected_currency) and current_currency != str(expected_currency).upper()
        changed = expected_amount is not None and abs(float(current_amount) - float(expected_amount)) > 0.01
        if (changed or currency_changed) and not allow_price_change:
            return {
                "booked": False,
                "requires_reconfirmation": True,
                "reason": "The fare changed after your initial selection.",
                "previous_amount": expected_amount,
                "current_amount": current_amount,
                "previous_currency": expected_currency,
                "currency": current_currency,
                "offer": latest,
            }

        booking = self.provider.book(offer_id, passengers, payment, payment_mode=payment_mode)
        return {
            "booked": True,
            "requires_reconfirmation": False,
            "rechecked_offer": latest,
            "booking": booking,
        }

    def pay_hold_with_sandbox_balance(self, order_id: str) -> dict[str, Any]:
        provider = self.provider
        if not isinstance(provider, DuffelProvider):
            raise FlightProviderError("Sandbox balance payment is supported by the Duffel provider only.")
        return provider.pay_hold_with_balance(order_id)

    def get_order(self, order_id: str) -> dict[str, Any]:
        if not isinstance(self.provider, DuffelProvider):
            raise FlightProviderError("Order retrieval is supported by the Duffel provider only.")
        return self.provider.get_order(order_id)

    def find_order_by_offer(self, offer_id: str) -> dict[str, Any] | None:
        if not isinstance(self.provider, DuffelProvider):
            raise FlightProviderError("Order lookup is supported by the Duffel provider only.")
        return self.provider.find_order_by_offer(offer_id)


    def cancellation_quote(self, order_id: str) -> dict[str, Any]:
        if not isinstance(self.provider, DuffelProvider):
            raise FlightProviderError("Cancellation is supported by the Duffel provider only.")
        return self.provider.create_cancellation_quote(order_id)

    def confirm_cancellation(self, cancellation_id: str) -> dict[str, Any]:
        if not isinstance(self.provider, DuffelProvider):
            raise FlightProviderError("Cancellation is supported by the Duffel provider only.")
        return self.provider.confirm_cancellation(cancellation_id)
