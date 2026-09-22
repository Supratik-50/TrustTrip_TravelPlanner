import hashlib
import hmac
import os
import unittest

from services.travel_planner import parse_trip_request, budget_from_live_data
from services.razorpay_service import RazorpayService


class PlannerAndPaymentTests(unittest.TestCase):
    def test_live_trip_constraints_are_extracted(self):
        result = parse_trip_request("Plan a 5-day trip from Kolkata to Delhi under ₹25,000.")
        self.assertEqual(result["origin"], "CCU")
        self.assertEqual(result["destination"], "DEL")
        self.assertEqual(result["duration_days"], 5)
        self.assertEqual(result["budget_amount"], 25000)
        self.assertTrue(result["flexible_dates"])

    def test_cheaper_replanning_reuses_previous_constraints(self):
        previous = {"origin": "CCU", "destination": "DEL", "duration_days": 5, "budget_amount": 25000}
        result = parse_trip_request("Make it cheaper.", previous)
        self.assertEqual(result["origin"], "CCU")
        self.assertEqual(result["destination"], "DEL")
        self.assertTrue(result["cheaper_mode"])


    def test_budget_agent_accepts_tavily_list_output(self):
        result = budget_from_live_data(
            {"duration_days": 5, "budget_amount": 25000},
            [{"price": 6000, "currency": "INR"}],
            [{"title": "Budget hotel", "content": "₹1800 per night"}],
        )
        self.assertEqual(result["hotel_price_source"], "live search snippet")
        self.assertEqual(result["hotel_estimate"], 7200.0)

    def test_razorpay_signature_algorithm(self):
        secret = "test_secret"
        os.environ["RAZORPAY_KEY_SECRET"] = secret
        service = RazorpayService()
        order_id = "order_test"
        payment_id = "pay_test"
        signature = hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
        self.assertTrue(service.verify_checkout_signature(order_id, payment_id, signature))
        self.assertFalse(service.verify_checkout_signature(order_id, payment_id, "bad"))


    def test_usd_to_inr_conversion_uses_provider_rate(self):
        from decimal import Decimal
        from unittest.mock import patch
        from services.currency_converter import CurrencyConverter

        with patch.object(CurrencyConverter, "get_rate", return_value={
            "date": "2026-09-08",
            "base": "USD",
            "quote": "INR",
            "rate": Decimal("90.50"),
        }):
            result = CurrencyConverter().convert(Decimal("67.89"), "USD", "INR")
        self.assertEqual(result["source_currency"], "USD")
        self.assertEqual(result["target_currency"], "INR")
        self.assertEqual(result["target_amount"], Decimal("6144.05"))
        self.assertEqual(result["rate"], Decimal("90.50"))



if __name__ == "__main__":

    unittest.main()


def test_ticket_pdf_is_generated_from_provider_order():
    from services.ticket_service import build_booking_pdf
    order = {
        "id": "ord_test", "booking_reference": "ABC123", "live_mode": False,
        "total_amount": "99.99", "total_currency": "USD",
        "created_at": "2026-09-08T12:00:00Z",
        "passengers": [{"given_name": "Test", "family_name": "User", "type": "adult", "born_on": "1990-01-01"}],
        "slices": [{"segments": [{"marketing_carrier_flight_number": "123", "origin": {"iata_code": "CCU"}, "destination": {"iata_code": "DEL"}, "departing_at": "2026-10-01T10:00:00Z", "arriving_at": "2026-10-01T12:30:00Z", "operating_carrier": {"name": "Demo Air"}}]}],
        "documents": [],
    }
    tx = {"booking_reference": "ABC123", "duffel_order_id": "ord_test", "amount": "9000", "currency": "INR", "razorpay_payment_id": "pay_test", "passenger_json": "[]"}
    pdf = build_booking_pdf(order, tx)
    assert pdf.startswith(b"%PDF")



def test_passenger_change_updates_trip_constraints():
    first = parse_trip_request("Plan Kolkata to Delhi for 2 adults on 2026-10-18")
    changed = parse_trip_request("Change it to 5 people", first)
    assert first["passenger_count"] == 2
    assert changed["passenger_count"] == 5
    assert changed["adults"] == 5

def test_flight_preference_is_extracted_for_review_replanning():
    first = parse_trip_request("Plan Kolkata to Delhi for 2 adults on 2026-10-18")
    changed = parse_trip_request("I want a cheaper morning flight", first)
    assert changed["flight_preference"] == "morning"
    assert changed["cheaper_mode"] is True
