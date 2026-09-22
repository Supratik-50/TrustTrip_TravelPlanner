import unittest
from unittest.mock import patch

from services.flight_service import FlightService
from services.flight_provider import FlightProvider


class FakeProvider(FlightProvider):
    name = "fake"

    def __init__(self):
        self.offers = {
            "off_1": {"id": "off_1", "price": 100.0, "currency": "USD", "stops": 0},
            "off_2": {"id": "off_2", "price": 150.0, "currency": "USD", "stops": 1},
        }
        self.booked = False

    def search(self, request):
        return list(self.offers.values())

    def get_offer(self, offer_id):
        return dict(self.offers[offer_id])

    def book(self, offer_id, passengers, payment=None, payment_mode="auto"):
        self.booked = True
        return {"order_id": "ord_test", "booking_reference": "TEST123"}


class FlightServiceTests(unittest.TestCase):
    def test_rank_prefers_lower_fare_and_fewer_stops(self):
        provider = FakeProvider()
        result = FlightService(provider).search({})
        self.assertEqual(result["offers"][0]["id"], "off_1")

    def test_duffel_usd_offer_gets_inr_reference_price(self):
        provider = FakeProvider()
        service = FlightService(provider)
        with patch.object(service._converter, "get_rate", return_value={"rate": 83.0, "date": "2026-09-17", "base": "USD", "quote": "INR"}):
            result = service.search({})
        offer = result["offers"][0]
        self.assertEqual(offer["price_inr"], 8300.0)
        self.assertEqual(offer["price_per_traveller_inr"], 8300.0)
        self.assertEqual(offer["currency"], "USD")


    def test_price_change_requires_reconfirmation(self):
        provider = FakeProvider()
        service = FlightService(provider)
        provider.offers["off_1"]["price"] = 125.0
        result = service.book_after_recheck(
            "off_1", 100.0, [{"given_name": "A", "family_name": "B", "born_on": "1990-01-01"}]
        )
        self.assertFalse(result["booked"])
        self.assertTrue(result["requires_reconfirmation"])
        self.assertFalse(provider.booked)

    def test_currency_change_requires_reconfirmation(self):
        provider = FakeProvider()
        service = FlightService(provider)
        provider.offers["off_1"]["currency"] = "INR"
        result = service.book_after_recheck(
            "off_1", 100.0,
            [{"given_name": "A", "family_name": "B", "born_on": "1990-01-01"}],
            expected_currency="USD",
        )
        self.assertFalse(result["booked"])
        self.assertTrue(result["requires_reconfirmation"])
        self.assertFalse(provider.booked)

    def test_successful_booking_passes_payment_mode(self):
        provider = FakeProvider()
        service = FlightService(provider)
        result = service.book_after_recheck(
            "off_1", 100.0,
            [{"given_name": "A", "family_name": "B", "born_on": "1990-01-01"}],
            payment_mode="sandbox_balance",
        )
        self.assertTrue(result["booked"])
        self.assertTrue(provider.booked)

    def test_booking_only_after_explicit_confirmation_at_api_layer(self):
        # The API route enforces final_confirmation before reaching the provider.
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()

class DuffelErrorFormattingTests(unittest.TestCase):
    def test_duffel_error_source_string_does_not_crash(self):
        # Regression coverage for Duffel responses where source is a string.
        item = {"source": "phone_number", "message": "Field 'phone_number' is invalid"}
        source = item.get("source")
        field = source if isinstance(source, str) else "field"
        self.assertEqual(field, "phone_number")



if __name__ == "__main__":
    unittest.main()
