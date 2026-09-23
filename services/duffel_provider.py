"""Duffel REST provider for sandbox/live flight shopping and ordering."""
from __future__ import annotations

import os
from typing import Any
from datetime import date, datetime

import requests

from .flight_provider import FlightProvider, FlightProviderError, FlightProviderTimeout, FlightProviderRateLimitError


def _departure_date_from_offer(offer: dict[str, Any]) -> date:
    """Return the first segment departure date from a normalized Duffel offer."""
    segments = offer.get("segments") or []
    for segment in segments:
        value = segment.get("departure") if isinstance(segment, dict) else None
        if value:
            text = str(value).strip()
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
            except ValueError:
                try:
                    return date.fromisoformat(text[:10])
                except ValueError:
                    pass

    raw = offer.get("raw") or {}
    for sl in raw.get("slices") or []:
        for segment in (sl.get("segments") or []):
            value = segment.get("departing_at")
            if value:
                text = str(value).strip()
                try:
                    return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
                except ValueError:
                    try:
                        return date.fromisoformat(text[:10])
                    except ValueError:
                        pass

    raise FlightProviderError("Duffel did not provide a valid departure date for this offer.")


def _passenger_type_for_dob(born_on: str, departure_date: date) -> str:
    """Map DOB to the Duffel passenger type required for the travel date."""
    dob = date.fromisoformat(str(born_on)[:10])
    if dob > departure_date:
        raise ValueError("date of birth is after departure date")
    age = departure_date.year - dob.year - ((departure_date.month, departure_date.day) < (dob.month, dob.day))
    if age < 2:
        return "infant_without_seat"
    if age < 18:
        return "child"
    return "adult"


def _normalize_phone(value: Any) -> str | None:
    """Normalize a passenger phone number to a Duffel-compatible E.164 value."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    compact = text.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    if compact.isdigit() and len(compact) == 10:
        compact = "+91" + compact
    if not compact.startswith("+") or not compact[1:].isdigit() or not (8 <= len(compact[1:]) <= 15):
        raise FlightProviderError("Passenger phone number must be a valid international phone number, e.g. +919876543210.")
    return compact


class DuffelProvider(FlightProvider):
    name = "duffel"
    BASE_URL = "https://api.duffel.com"

    def __init__(
        self,
        token: str | None = None,
        timeout: int | None = None,
        order_timeout: int | None = None,
    ):
        self.mode = os.getenv("DUFFEL_MODE", "test").strip().lower()
        if self.mode not in {"test", "live"}:
            raise FlightProviderError("DUFFEL_MODE must be 'test' or 'live'.")
        configured_token = (
            os.getenv("DUFFEL_TEST_API_TOKEN") if self.mode == "test"
            else os.getenv("DUFFEL_LIVE_API_TOKEN")
        )
        # Backward-compatible fallback for older local .env files. New
        # deployments should use the explicit test/live variables above.
        self.token = token or configured_token or os.getenv("DUFFEL_ACCESS_TOKEN")
        self.timeout = timeout or int(os.getenv("DUFFEL_API_TIMEOUT_SECONDS", "30"))
        # Duffel documents that flight order creation can take up to 120s and
        # recommends an HTTP client timeout of at least 130s.  Keep ordinary
        # search/recheck requests short, but give the transactional order
        # request enough time to finish.
        self.order_timeout = order_timeout or int(
            os.getenv("DUFFEL_ORDER_TIMEOUT_SECONDS", "150")
        )
        if self.timeout < 1 or self.order_timeout < 1:
            raise FlightProviderError("Duffel timeouts must be positive integers.")
        if not self.token:
            raise FlightProviderError(
                "DUFFEL_ACCESS_TOKEN is missing. Add a Duffel test token to .env."
            )
        self.test_mode = self.token.startswith("duffel_test_")
        if self.mode == "test" and not self.test_mode:
            raise FlightProviderError("DUFFEL_MODE=test requires a duffel_test_ token.")
        if self.mode == "live" and self.test_mode:
            raise FlightProviderError("DUFFEL_MODE=live requires a live Duffel token, not a test token.")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Duffel-Version": "v2",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        request_timeout: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        timeout = request_timeout or self.timeout
        try:
            response = requests.request(
                method,
                f"{self.BASE_URL}{path}",
                headers=self.headers,
                timeout=timeout,
                **kwargs,
            )
        except requests.Timeout as exc:
            raise FlightProviderTimeout(
                f"Duffel timed out after {timeout} seconds while calling {path}. "
                "Flight order creation can take up to about 120 seconds; the booking "
                "request is configured with a longer timeout. Do not retry an order "
                "automatically because a timeout can leave the airline-side booking ambiguous."
            ) from exc
        except requests.RequestException as exc:
            raise FlightProviderError(f"Duffel request failed: {exc}") from exc

        if not response.ok:
            if response.status_code == 429:
                raise FlightProviderRateLimitError("Duffel API rate limit exceeded (HTTP 429).")
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            if response.status_code == 422 and isinstance(detail, dict):
                errors = detail.get("errors") or []
                messages = []
                for item in errors:
                    # Duffel error payloads can vary: `source` may be an object
                    # in some responses and a string/null in others. Never let
                    # error formatting itself crash the API with a 500.
                    if isinstance(item, dict):
                        source = item.get("source")
                        if isinstance(source, dict):
                            field = source.get("field") or "field"
                        elif isinstance(source, str) and source.strip():
                            field = source.strip()
                        else:
                            field = "field"
                        message = item.get("message") or item.get("title") or "invalid value"
                    else:
                        field = "field"
                        message = str(item) or "invalid value"
                    messages.append(f"{field}: {message}")
                if messages:
                    raise FlightProviderError("Duffel rejected the passenger details: " + "; ".join(messages))
            raise FlightProviderError(f"Duffel API returned HTTP {response.status_code}: {detail}")
        try:
            return response.json()
        except ValueError as exc:
            raise FlightProviderError("Duffel returned invalid JSON.") from exc

    @staticmethod
    def _normalize_offer(offer: dict[str, Any]) -> dict[str, Any]:
        slices = offer.get("slices") or []
        legs = []
        stops = 0
        for sl in slices:
            segments = sl.get("segments") or []
            stops += max(0, len(segments) - 1)
            for seg in segments:
                operating = seg.get("operating_carrier") or {}
                marketing = seg.get("marketing_carrier") or {}
                legs.append(
                    {
                        "origin": (seg.get("origin") or {}).get("iata_code"),
                        "origin_name": (seg.get("origin") or {}).get("name"),
                        "destination": (seg.get("destination") or {}).get("iata_code"),
                        "destination_name": (seg.get("destination") or {}).get("name"),
                        "departure": seg.get("departing_at"),
                        "arrival": seg.get("arriving_at"),
                        "duration": seg.get("duration"),
                        "operating_carrier": operating.get("name"),
                        "operating_carrier_iata": operating.get("iata_code"),
                        "marketing_carrier": marketing.get("name"),
                        "marketing_carrier_iata": marketing.get("iata_code"),
                        "flight_number": seg.get("marketing_carrier_flight_number"),
                    }
                )

        total = offer.get("total_amount")
        currency = offer.get("total_currency")
        payment_requirements = offer.get("payment_requirements") or {}
        offer_passengers = offer.get("passengers") or []

        # Duffel does not expose a reliable "remaining inventory" number on
        # ordinary offers. Keep an explicit field only if a provider payload
        # contains one; never infer inventory from the passenger list because
        # that list represents the passengers used in this offer request.
        available_seats = None
        for key in ("available_seats", "remaining_seats", "seats_available"):
            value = offer.get(key)
            if isinstance(value, int) and value >= 0:
                available_seats = value
                break

        return {
            "id": offer.get("id"),
            "provider": "duffel",
            "price": float(total) if total is not None else None,
            "currency": currency,
            "expires_at": offer.get("expires_at"),
            "owner": (offer.get("owner") or {}).get("name"),
            "owner_iata": (offer.get("owner") or {}).get("iata_code"),
            "stops": stops,
            "segments": legs,
            "payment_requirements": payment_requirements,
            "passengers": offer_passengers,
            "passenger_count": len(offer_passengers),
            "available_seats": available_seats,
            "inventory_note": (
                "Exact remaining inventory was not supplied by the airline/provider."
                if available_seats is None else "Provider supplied an explicit available-seat count."
            ),
            "raw": offer,
        }

    def search(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        slices = request.get("slices") or []
        passengers = request.get("passengers") or [{"type": "adult"}]
        payload = {
            "data": {
                "slices": slices,
                "passengers": passengers,
                "cabin_class": request.get("cabin_class", "economy"),
                "max_connections": request.get("max_connections", 2),
            }
        }
        if request.get("return_offers") is not None:
            payload["data"]["return_offers"] = bool(request["return_offers"])

        supplier_timeout_ms = int(os.getenv("DUFFEL_SUPPLIER_TIMEOUT_MS", "45000"))
        supplier_timeout_ms = max(2000, min(60000, supplier_timeout_ms))
        result = self._request(
            "POST",
            "/air/offer_requests",
            params={"return_offers": "true", "view": "offers", "supplier_timeout": str(supplier_timeout_ms)},
            json=payload,
        )
        offers = (result.get("data") or {}).get("offers") or []
        return [self._normalize_offer(item) for item in offers]


    @staticmethod
    def _offer_signature(offer: dict[str, Any]) -> tuple:
        """Build a stable itinerary signature for matching a selected flight in a fresh search."""
        raw = offer.get("raw") if isinstance(offer.get("raw"), dict) else offer
        slices = raw.get("slices") or []
        sig = []
        for sl in slices:
            for seg in sl.get("segments") or []:
                origin = (seg.get("origin") or {}).get("iata_code") or seg.get("origin")
                destination = (seg.get("destination") or {}).get("iata_code") or seg.get("destination")
                marketing = seg.get("marketing_carrier") or {}
                operating = seg.get("operating_carrier") or {}
                number = seg.get("marketing_carrier_flight_number") or seg.get("flight_number")
                departure = seg.get("departing_at") or seg.get("departure")
                sig.append((str(origin or "").upper(), str(destination or "").upper(),
                            str(marketing.get("iata_code") or operating.get("iata_code") or "").upper(),
                            str(number or ""), str(departure or "")[:10]))
        return tuple(sig)

    def refresh_bookable_offer(self, offer_id: str, search_context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Create a NEW Duffel offer request for a selected itinerary.

        Duffel permits only one order per offer request. Therefore a second
        purchase from the same on-screen search must never reuse the old offer
        request. We repeat the original search and select the same itinerary
        from the new offer request before payment is created.
        """
        selected = None
        if search_context:
            selected = search_context.get("selected_offer") or {}

        if not selected:
            selected = self.get_offer(offer_id)

        context = search_context or {}
        slices = context.get("slices") or []
        passengers = context.get("passengers") or []
        cabin_class = context.get("cabin_class", "economy")
        max_connections = context.get("max_connections", 2)
        if not slices:
            raw = selected.get("raw") or {}
            slices = raw.get("slices") or []
        if not passengers:
            raw_passengers = (selected.get("raw") or {}).get("passengers") or selected.get("passengers") or []
            passengers = [{"type": p.get("type", "adult")} for p in raw_passengers] or [{"type": "adult"}]
        if not slices:
            raise FlightProviderError(
                "This flight result cannot be refreshed because its original search criteria are unavailable. Please run a new flight search."
            )

        fresh = self.search({
            "slices": slices,
            "passengers": passengers,
            "cabin_class": cabin_class,
            "max_connections": max_connections,
        })
        target_sig = self._offer_signature(selected)
        candidates = [o for o in fresh if self._offer_signature(o) == target_sig]

        # If flight numbers are unavailable, use route + carrier + departure date
        # as a conservative fallback. Never silently change the selected route.
        if not candidates and target_sig:
            target_route = tuple((a, b, c, d) for a, b, c, d, _ in target_sig)
            candidates = [
                o for o in fresh
                if tuple((a, b, c, d) for a, b, c, d, _ in self._offer_signature(o)) == target_route
            ]

        if not candidates:
            raise FlightProviderError(
                "The selected flight is no longer available in a fresh supplier search. Please choose the current offer shown in the search results."
            )

        old_price = float(selected.get("price") or 0)
        candidates.sort(key=lambda o: abs(float(o.get("price") or 0) - old_price))
        fresh_offer = candidates[0]
        # Perform Duffel's final GET refresh on the newly-created offer before
        # exposing it to the payment flow.
        return self.get_offer(fresh_offer["id"])

    def get_offer(self, offer_id: str) -> dict[str, Any]:
        if not offer_id or not offer_id.startswith("off_"):
            raise FlightProviderError("A valid Duffel offer ID is required.")
        result = self._request("GET", f"/air/offers/{offer_id}")
        return self._normalize_offer(result.get("data") or {})

    def book(
        self,
        offer_id: str,
        passengers: list[dict[str, Any]],
        payment: dict[str, Any] | None = None,
        payment_mode: str = "auto",
    ) -> dict[str, Any]:
        """Create an order after the application has obtained final confirmation.

        payment_mode:
          - auto: hold when possible; instant with explicit payment when supplied
          - hold: unpaid hold order (only when the offer permits it)
          - sandbox_balance: instant order paid from Duffel's test balance

        The sandbox_balance path is deliberately test-token-only. It lets the
        final-year-project demo exercise a complete pay-now booking flow without
        collecting or storing real card details.
        """
        if not passengers:
            raise FlightProviderError("At least one passenger is required.")

        latest = self.get_offer(offer_id)
        offer_passengers = latest.get("passengers") or []
        if len(offer_passengers) != len(passengers):
            raise FlightProviderError(
                f"Passenger count mismatch: the selected offer expects {len(offer_passengers)} passenger(s), "
                f"but {len(passengers)} passenger(s) were supplied."
            )

        normalized_passengers: list[dict[str, Any]] = []
        departure_date = _departure_date_from_offer(latest)
        for supplied, offer_passenger in zip(passengers, offer_passengers):
            passenger = dict(supplied)
            passenger_id = offer_passenger.get("id")
            if not passenger_id:
                raise FlightProviderError(
                    "Duffel did not return a passenger ID for the selected offer."
                )
            passenger["id"] = passenger_id
            # Duffel validates passenger type against age on the travel date.
            # Derive it from DOB instead of trusting a stale/default UI value.
            if passenger.get("born_on"):
                try:
                    passenger["type"] = _passenger_type_for_dob(passenger["born_on"], departure_date)
                except (TypeError, ValueError):
                    raise FlightProviderError("Passenger date of birth must be a valid YYYY-MM-DD date.")
            phone = _normalize_phone(passenger.get("phone_number"))
            if phone:
                passenger["phone_number"] = phone
            else:
                passenger.pop("phone_number", None)
            if not str(passenger.get("email") or "").strip():
                passenger.pop("email", None)
            normalized_passengers.append(passenger)

        payment_requirements = latest.get("payment_requirements") or {}
        requires_instant_payment = bool(
            payment_requirements.get("requires_instant_payment")
        )

        mode = (payment_mode or "auto").strip().lower()
        if mode not in {"auto", "hold", "sandbox_balance", "duffel_balance", "card"}:
            raise FlightProviderError(
                "Unsupported payment mode. Use auto, hold, sandbox_balance, or card."
            )

        # A hold is only legal when Duffel says payment is not required instantly.
        if mode == "hold" or (mode == "auto" and payment is None and not requires_instant_payment):
            if requires_instant_payment:
                raise FlightProviderError(
                    "This offer requires instant payment and cannot be held. Choose Pay Now (Sandbox) "
                    "or select an offer that supports payment later."
                )
            order_type = "hold"
            data: dict[str, Any] = {
                "type": order_type,
                "selected_offers": [offer_id],
                "passengers": normalized_passengers,
            }

        elif mode in {"sandbox_balance", "duffel_balance"}:
            if mode == "sandbox_balance" and not self.test_mode:
                raise FlightProviderError(
                    "Sandbox balance payment is available only with a Duffel test token."
                )
            order_type = "instant"
            data = {
                "type": order_type,
                "selected_offers": [offer_id],
                "passengers": normalized_passengers,
                "payments": [{
                    "type": "balance",
                    "currency": latest.get("currency"),
                    "amount": f"{float(latest.get('price')):.2f}",
                }],
            }

        elif mode == "card":
            if not payment:
                raise FlightProviderError(
                    "Card payment was selected but no authenticated Duffel payment details were supplied."
                )
            order_type = "instant"
            data = {
                "type": order_type,
                "selected_offers": [offer_id],
                "passengers": normalized_passengers,
                "payments": [payment],
            }

        else:
            # Explicit payment object supplied by a future PCI/3DS integration.
            if not payment:
                raise FlightProviderError(
                    "Instant booking requires an authenticated payment method."
                )
            order_type = "instant"
            data = {
                "type": order_type,
                "selected_offers": [offer_id],
                "passengers": normalized_passengers,
                "payments": [payment],
            }

        result = self._request(
            "POST",
            "/air/orders",
            request_timeout=self.order_timeout,
            json={"data": data},
        )
        order = result.get("data") or {}
        # Duffel may return 202 for an asynchronously-created order. Keep the
        # provider status so the application can poll the order safely instead
        # of assuming a PNR exists immediately.
        return {
            "provider": "duffel",
            "order_id": order.get("id"),
            "booking_reference": order.get("booking_reference"),
            "status": order.get("type"),
            "order_type": order.get("type"),
            "total_amount": order.get("total_amount"),
            "total_currency": order.get("total_currency"),
            "payment_required_by": order.get("payment_required_by"),
            "live_mode": order.get("live_mode", False),
            "payment_mode": mode if mode in {"sandbox_balance", "duffel_balance"} else order_type,
            "documents": order.get("documents") or [],
            "raw": order,
        }

    def get_order(self, order_id: str) -> dict[str, Any]:
        if not order_id or not order_id.startswith("ord_"):
            raise FlightProviderError("A valid Duffel order ID is required.")
        result = self._request("GET", f"/air/orders/{order_id}")
        return result.get("data") or {}


    def find_order_by_offer(self, offer_id: str) -> dict[str, Any] | None:
        """Find an existing order by offer ID after an ambiguous create response."""
        result = self._request("GET", "/air/orders", params={"offer_id": offer_id, "limit": 10})
        orders = result.get("data") or []
        if not orders:
            return None
        # Prefer the newest matching order.
        return sorted(orders, key=lambda x: x.get("created_at") or "", reverse=True)[0]


    def create_cancellation_quote(self, order_id: str) -> dict[str, Any]:
        order = self.get_order(order_id)
        if "cancel" not in (order.get("available_actions") or []):
            raise FlightProviderError("This airline order does not currently support API cancellation.")
        result = self._request(
            "POST", "/air/order_cancellations",
            request_timeout=self.order_timeout,
            json={"data": {"order_id": order_id}},
        )
        return result.get("data") or {}

    def confirm_cancellation(self, cancellation_id: str) -> dict[str, Any]:
        if not cancellation_id or not cancellation_id.startswith("ore_"):
            raise FlightProviderError("A valid Duffel cancellation ID is required.")
        result = self._request(
            "POST", f"/air/order_cancellations/{cancellation_id}/actions/confirm",
            request_timeout=self.order_timeout,
        )
        return result.get("data") or {}

    def pay_hold_with_balance(self, order_id: str) -> dict[str, Any]:
        """Pay an existing hold order with Duffel's test balance.

        This is a safe demo payment path: it is enabled only for duffel_test_
        tokens and never accepts or stores a customer's real card number.
        """
        if not self.test_mode:
            raise FlightProviderError(
                "Sandbox balance payment is available only with a Duffel test token."
            )
        order = self.get_order(order_id)
        if order.get("type") != "hold":
            raise FlightProviderError("Only hold orders can be paid through this endpoint.")
        amount = order.get("total_amount")
        currency = order.get("total_currency")
        if amount is None or not currency:
            raise FlightProviderError("The hold order does not contain a payable total.")

        result = self._request(
            "POST",
            "/air/payments",
            request_timeout=self.order_timeout,
            json={
                "data": {
                    "order_id": order_id,
                    "payment": {
                        "type": "balance",
                        "amount": str(amount),
                        "currency": currency,
                    },
                }
            },
        )
        payment = result.get("data") or {}
        return {
            "provider": "duffel",
            "order_id": order_id,
            "payment_id": payment.get("id"),
            "payment_status": payment.get("status"),
            "payment_type": payment.get("type"),
            "amount": payment.get("amount", amount),
            "currency": payment.get("currency", currency),
            "live_mode": payment.get("live_mode", False),
            "raw": payment,
        }

