from __future__ import annotations
import hashlib, json, os, time
from typing import Any
import requests
from .hotel_provider import HotelProviderError

class HotelbedsProvider:
    name = "hotelbeds"
    BASE = "https://api.test.hotelbeds.com"
    def __init__(self):
        self.mode = os.getenv("HOTELBEDS_MODE", "test").lower()
        self.base = "https://api.hotelbeds.com" if self.mode == "live" else self.BASE
        self.api_key = os.getenv("HOTELBEDS_API_KEY")
        self.secret = os.getenv("HOTELBEDS_SECRET")
        self.timeout = int(os.getenv("HOTELBEDS_TIMEOUT_SECONDS", "30"))
        self.cert = os.getenv("HOTELBEDS_CLIENT_CERT")
        self.key = os.getenv("HOTELBEDS_CLIENT_KEY")
        if not self.api_key or not self.secret:
            raise HotelProviderError("Hotelbeds is not configured. Add HOTELBEDS_API_KEY and HOTELBEDS_SECRET to .env.")

    def _headers(self):
        sig = hashlib.sha256(f"{self.api_key}{self.secret}{int(time.time())}".encode()).hexdigest()
        return {"Api-key": self.api_key, "X-Signature": sig, "Accept": "application/json", "Content-Type": "application/json"}

    def _request(self, method, path, **kwargs):
        cert = (self.cert, self.key) if self.cert and self.key else None
        try:
            r = requests.request(method, self.base + path, headers=self._headers(), cert=cert, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise HotelProviderError(f"Hotelbeds request failed: {exc}") from exc
        try: data = r.json()
        except ValueError: data = {"error": r.text[:500]}
        if not r.ok:
            detail = data.get("error") if isinstance(data, dict) else data
            if not (self.cert and self.key):
                detail = "Hotelbeds Booking API now requires mTLS for availability/checkrate/booking. Configure HOTELBEDS_CLIENT_CERT and HOTELBEDS_CLIENT_KEY. " + str(detail)
            raise HotelProviderError(f"{detail}")
        return data

    def _destination_code(self, request):
        explicit = request.get("hotelbeds_destination_code")
        if explicit: return str(explicit).upper()
        mapping_raw = os.getenv("HOTELBEDS_DESTINATION_CODES_JSON", "{}")
        try: mapping = json.loads(mapping_raw)
        except Exception: mapping = {}
        destination = str(request.get("destination") or "").strip().lower()
        return str(mapping.get(destination) or mapping.get(destination.title()) or "").upper()

    def search(self, request):
        destination = self._destination_code(request)
        if not destination:
            raise HotelProviderError("Hotelbeds search needs a configured destination code. Add it to HOTELBEDS_DESTINATION_CODES_JSON for this city.")
        body = {
            "stay": {"checkIn": request["checkin"], "checkOut": request["checkout"]},
            "occupancies": [{"rooms": int(request.get("rooms", 1)), "adults": int(request.get("adults", 1)), "children": int(request.get("children", 0))}],
            "destination": {"code": destination},
        }
        data = self._request("POST", "/hotel-api/1.0/hotels", json=body)
        offers=[]
        for hotel in ((data.get("hotels") or {}).get("hotels") or []):
            rates = hotel.get("rooms") or []
            for room in rates:
                for rate in room.get("rates") or []:
                    amount = rate.get("net")
                    if amount is None: continue

                    # hotel_name: only emit if provider explicitly supplies it
                    raw_hotel_name = hotel.get("name")
                    hotel_name = str(raw_hotel_name).strip() if raw_hotel_name and str(raw_hotel_name).strip() else None

                    # room_name: only emit if provider explicitly supplies it (never fall back to roomCode)
                    raw_room_name = room.get("name")
                    room_name = str(raw_room_name).strip() if raw_room_name and str(raw_room_name).strip() else None

                    # meal_plan / board_name: only emit if provider explicitly supplies it
                    raw_board = rate.get("boardName")
                    board_name = str(raw_board).strip() if raw_board and str(raw_board).strip() else None

                    # refundable: only emit when cancellation policy is EXPLICITLY known
                    cancellation_data = rate.get("cancellationPolicies")
                    refundable = None

                    if isinstance(cancellation_data, dict):
                        tag = str(cancellation_data.get("refundableTag") or cancellation_data.get("status") or cancellation_data.get("type") or "").strip().upper()
                        if tag in ("RFN", "REFUNDABLE", "FREE_CANCELLATION"):
                            refundable = True
                        elif tag in ("NRF", "NON_REFUNDABLE", "NONREFUNDABLE"):
                            refundable = False
                        elif cancellation_data.get("refundable") is True:
                            refundable = True
                        elif cancellation_data.get("refundable") is False:
                            refundable = False
                    elif isinstance(cancellation_data, list) and len(cancellation_data) > 0:
                        for policy in cancellation_data:
                            if isinstance(policy, dict):
                                tag = str(policy.get("refundableTag") or policy.get("status") or policy.get("type") or "").strip().upper()
                                if tag in ("RFN", "REFUNDABLE", "FREE_CANCELLATION"):
                                    refundable = True
                                    break
                                elif tag in ("NRF", "NON_REFUNDABLE", "NONREFUNDABLE"):
                                    refundable = False
                                    break
                                elif policy.get("refundable") is True:
                                    refundable = True
                                    break
                                elif policy.get("refundable") is False:
                                    refundable = False
                                    break

                    rate_type = rate.get("rateType")
                    # Note: rateType (BOOKABLE, NONREFUNDABLE, etc.) must NEVER determine refundability — remains None if no explicit cancellation policy

                    # currency: only emit if provider explicitly supplies it — never default to EUR
                    raw_currency = data.get("currency")
                    currency = str(raw_currency).strip().upper() if raw_currency and str(raw_currency).strip() else None

                    offers.append({
                        "id": f"hotelbeds:{rate.get('rateKey')}", "provider": self.name, "provider_offer_id": rate.get("rateKey"),
                        "bookable": True, "hotel_id": hotel.get("code"), "hotel_name": hotel_name,
                        "address": "", "rating": None, "stars": hotel.get("categoryName"), "photo": "",
                        "room_name": room_name, "board_name": board_name,
                        "cancellation": rate.get("cancellationPolicies") or [], "refundable": refundable,
                        "price": float(amount), "currency": currency, "rate_type": rate_type, "raw": rate,
                        "rooms": int(request.get("rooms", 1)), "adults": int(request.get("adults", 1)), "children": int(request.get("children", 0)),
                        "checkin": request.get("checkin"), "checkout": request.get("checkout"),
                    })
        return offers

    def prebook(self, offer):
        if offer.get("rate_type") == "RECHECK":
            data = self._request("POST", "/hotel-api/1.0/checkrates", json={"rooms": [{"rateKey": offer["provider_offer_id"]}]})
            return {"provider": self.name, "prebook_id": offer["provider_offer_id"], "rooms": int(offer.get("rooms") or 1), "raw": data}
        return {"provider": self.name, "prebook_id": offer["provider_offer_id"], "rooms": int(offer.get("rooms") or 1), "raw": offer.get("raw", {})}

    def book(self, prebook, holder, guests, client_reference):
        room_count = max(1, int(prebook.get("rooms") or 1))
        rooms = [{"rateKey": prebook["prebook_id"], "paxes": [{"roomId": room_no, "type": "AD", "age": 30}]} for room_no in range(1, room_count + 1)]
        data = self._request("POST", "/hotel-api/1.0/bookings", json={"holder": holder, "rooms": rooms, "clientReference": client_reference, "remark": "Trust Trip MAS"})
        booking = data.get("booking") or data
        return {"provider": self.name, "booking_id": booking.get("reference"), "confirmation_code": booking.get("reference"), "status": "CONFIRMED", "raw": data}

    def reconcile(self, client_reference: str) -> tuple[str, dict[str, Any] | None]:
        """Inquire supplier for existing booking by clientReference (HRP-04)."""
        try:
            data = self._request("GET", "/hotel-api/1.0/bookings", params={"clientReference": client_reference})
        except Exception:
            return ("UNCERTAIN", None)

        b_obj = data.get("bookings") if isinstance(data, dict) else None
        bookings = b_obj.get("bookings") if isinstance(b_obj, dict) else (b_obj if isinstance(b_obj, list) else [])
        if bookings:
            target = bookings[0]
            status = str(target.get("status") or "CONFIRMED").upper()
            if status in {"CONFIRMED", "BOOKED"}:
                return ("CONFIRMED", {
                    "provider": self.name,
                    "booking_id": target.get("reference"),
                    "confirmation_code": target.get("reference"),
                    "status": "CONFIRMED",
                    "raw": target,
                })
        return ("NOT_FOUND", None)
