from __future__ import annotations
from datetime import date
import os
from typing import Any
import requests
from .hotel_provider import HotelProviderError

class LiteAPIProvider:
    name = "liteapi"
    SEARCH_URL = "https://api.liteapi.travel/v3.0/hotels/rates"
    BOOK_URL = "https://book.liteapi.travel/v3.0"

    def __init__(self):
        self.api_key = os.getenv("LITEAPI_SANDBOX_API_KEY") if os.getenv("LITEAPI_MODE", "sandbox").lower() == "sandbox" else os.getenv("LITEAPI_PRODUCTION_API_KEY")
        self.mode = os.getenv("LITEAPI_MODE", "sandbox").lower()
        self.timeout = int(os.getenv("LITEAPI_TIMEOUT_SECONDS", "30"))
        if not self.api_key:
            raise HotelProviderError("LiteAPI is not configured. Add LITEAPI_SANDBOX_API_KEY (sandbox) or LITEAPI_PRODUCTION_API_KEY (production) to .env.")

    @property
    def headers(self):
        return {"X-API-Key": self.api_key, "accept": "application/json", "content-type": "application/json"}

    def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            r = requests.request(method, url, headers=self.headers, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise HotelProviderError(f"LiteAPI request failed: {exc}") from exc
        try:
            data = r.json()
        except ValueError:
            data = {"error": {"message": r.text[:500]}}
        if not r.ok:
            err = data.get("error") if isinstance(data, dict) else None
            raise HotelProviderError((err or {}).get("description") or (err or {}).get("message") or f"LiteAPI HTTP {r.status_code}")
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            raise HotelProviderError(err.get("description") or err.get("message") or "LiteAPI returned an error")
        return data

    def search(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        rooms = max(1, int(request.get("rooms", 1)))
        occupancy = {"adults": int(request.get("adults", 1)), "children": [{"age": int(a)} for a in request.get("children_ages", [])]}
        body = {
            "occupancies": [dict(occupancy) for _ in range(rooms)],
            "currency": str(request.get("currency", "USD")).upper(),
            "guestNationality": str(request.get("guest_nationality", "IN")).upper(),
            "checkin": request["checkin"],
            "checkout": request["checkout"],
            "maxRatesPerHotel": 1,
            "roomMapping": True,
            "includeHotelData": True,
        }
        if request.get("place_id"):
            body["placeId"] = request["place_id"]
        else:
            body["aiSearch"] = str(request.get("destination") or "")
        data = self._request("POST", self.SEARCH_URL, json=body)
        hotels = {str(h.get("id")): h for h in (data.get("hotels") or []) if isinstance(h, dict)}
        # Compute stay nights from checkin and checkout dates
        try:
            d_in = date.fromisoformat(str(request.get("checkin") or "").split("T")[0])
            d_out = date.fromisoformat(str(request.get("checkout") or "").split("T")[0])
            nights = max(1, (d_out - d_in).days)
        except Exception:
            nights = 1

        offers: list[dict[str, Any]] = []
        for item in data.get("data") or []:
            if not isinstance(item, dict):
                continue
            hotel_id = str(item.get("hotelId") or "")
            hotel = hotels.get(hotel_id, {})
            rates = []
            for room in item.get("roomTypes") or []:
                for rate in room.get("rates") or []:
                    total = ((rate.get("retailRate") or {}).get("total") or [{}])[0]
                    amount = total.get("amount")
                    if amount is None:
                        continue
                    rates.append((float(amount), room, rate, total))
            if not rates:
                continue
            amount, room, rate, total = min(rates, key=lambda x: x[0])
            cancellation = rate.get("cancellationPolicies") or {}
            offer_id = rate.get("offerId") or room.get("offerId")

            # hotel_name: only emit if provider explicitly supplies it
            raw_hotel_name = hotel.get("name") or item.get("hotelName")
            hotel_name = str(raw_hotel_name).strip() if raw_hotel_name and str(raw_hotel_name).strip() else None

            # room_name: only emit if provider explicitly supplies it
            raw_room_name = rate.get("name") or room.get("name")
            room_name = str(raw_room_name).strip() if raw_room_name and str(raw_room_name).strip() else None

            # meal_plan / board_name: only emit if provider explicitly supplies it
            raw_board = rate.get("boardName")
            board_name = str(raw_board).strip() if raw_board and str(raw_board).strip() else None

            # refundable: only emit when cancellation tag is EXPLICITLY known (RFN = refundable, NRF = non-refundable)
            refundable_tag = cancellation.get("refundableTag") if isinstance(cancellation, dict) else None
            if refundable_tag == "RFN":
                refundable = True
            elif refundable_tag == "NRF":
                refundable = False
            else:
                # Missing or unknown tag — do NOT infer
                refundable = None

            # currency: only emit if provider explicitly supplies it — never default to USD
            raw_currency = total.get("currency")
            currency = str(raw_currency).strip().upper() if raw_currency and str(raw_currency).strip() else None

            offers.append({
                "id": f"liteapi:{offer_id}",
                "provider": self.name,
                "provider_offer_id": offer_id,
                "bookable": bool(offer_id),
                "hotel_id": hotel_id,
                "hotel_name": hotel_name,
                "photo": hotel.get("main_photo") or hotel.get("mainPhoto") or "",
                "address": hotel.get("address") or hotel.get("formattedAddress") or "",
                "rating": hotel.get("rating"),
                "stars": hotel.get("stars") or hotel.get("starRating"),
                "tags": hotel.get("tags") or [],
                "room_name": room_name,
                "board_name": board_name,
                "cancellation": cancellation,
                "refundable": refundable,
                "price": amount,
                "total_price": amount,
                "pricing_basis": "full_stay",
                "nights": nights,
                "currency": currency,
                "rooms": rooms,
                "adults": int(request.get("adults", 1)),
                "children": int(request.get("children", 0)),
                "checkin": request.get("checkin"),
                "checkout": request.get("checkout"),
                "raw": rate,
            })
        return offers

    def prebook(self, offer: dict[str, Any]) -> dict[str, Any]:
        payload = {"offerId": offer["provider_offer_id"], "usePaymentSdk": False}
        data = self._request("POST", f"{self.BOOK_URL}/rates/prebook", json=payload)
        return {"provider": self.name, "prebook_id": data.get("data", {}).get("prebookId") or data.get("prebookId"), "rooms": int(offer.get("rooms") or 1), "raw": data}

    def book(self, prebook: dict[str, Any], holder: dict[str, Any], guests: list[dict[str, Any]], client_reference: str) -> dict[str, Any]:
        method = os.getenv("LITEAPI_BOOK_PAYMENT_METHOD", "ACC_CREDIT_CARD" if self.mode == "sandbox" else "WALLET").upper()
        rooms = max(1, int(prebook.get("rooms") or 1))
        lead = dict(guests[0]) if guests else {"occupancyNumber": 1, **holder}
        expanded_guests = []
        for room_number in range(1, rooms + 1):
            guest = dict(lead)
            guest["occupancyNumber"] = room_number
            expanded_guests.append(guest)
        payload = {
            "prebookId": prebook["prebook_id"],
            "clientReference": client_reference,
            "holder": holder,
            "guests": expanded_guests,
            "payment": {"method": method},
        }
        data = self._request("POST", f"{self.BOOK_URL}/rates/book", json=payload)
        d = data.get("data") or data
        return {
            "provider": self.name,
            "booking_id": d.get("bookingId"),
            "confirmation_code": d.get("hotelConfirmationCode") or d.get("confirmationCode"),
            "status": d.get("status") or "CONFIRMED",
            "raw": data,
        }

    def reconcile(self, client_reference: str) -> tuple[str, dict[str, Any] | None]:
        """Inquire supplier for existing booking by clientReference (HRP-04)."""
        try:
            data = self._request("GET", f"{self.BOOK_URL}/bookings", params={"clientReference": client_reference})
        except Exception:
            return ("UNCERTAIN", None)

        d = data.get("data") if isinstance(data, dict) else data
        bookings = d if isinstance(d, list) else ([d] if isinstance(d, dict) and d.get("bookingId") else [])
        if bookings:
            target = bookings[0]
            status = str(target.get("status") or "CONFIRMED").upper()
            if status in {"CONFIRMED", "BOOKED", "SUCCESS"}:
                return ("CONFIRMED", {
                    "provider": self.name,
                    "booking_id": target.get("bookingId"),
                    "confirmation_code": target.get("hotelConfirmationCode") or target.get("confirmationCode") or target.get("bookingId"),
                    "status": "CONFIRMED",
                    "raw": target,
                })
        return ("NOT_FOUND", None)
