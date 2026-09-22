"""Tests for Hardened Hotel Booking PDF Renderer."""
from __future__ import annotations

from services.hotel_booking_pdf import build_hotel_booking_pdf


def test_pdf_hardened_against_extremely_long_text():
    """Verify PDF generates without error when hotel name and room description exceed 150 chars."""
    record = {
        "id": "tx_long_123",
        "provider": "liteapi",
        "hotel_name": "The Royal Magnificent Imperial Palace Beachfront Resort & Ultra Luxury Wellness Spa Grand Suites At Waterfront Marina",
        "booking_status": "booked",
        "amount_inr": 85000.0,
        "offer_json": {
            "room_name": "Super Executive Royal Presidential Penthouse Suite With Private Plunge Pool, Panoramic Ocean View, Butler Service and VIP Lounge Access",
            "board_name": "All Inclusive Luxury Gourmet Dining With 24 Hours Room Service and Premium Beverages",
            "checkin": "2026-11-10",
            "checkout": "2026-11-15",
            "rooms": 2,
            "cancellation": {
                "description": "Full refund if cancelled at least 48 hours before check-in date. If cancelled within 48 hours of check-in date, 50% of the total reservation cost will be assessed as a penalty. No-shows are charged 100% of the reservation total."
            },
        },
        "holder_json": {
            "firstName": "Alexander-Maximilian",
            "lastName": "Montgomery-Cunningham-Ponsonby",
            "email": "alexander.maximilian.montgomery.cunningham@prestigious-enterprise.example.com",
            "phone": "+141555526712345",
        },
        "guests_json": [
            {"firstName": "Alexander", "lastName": "Montgomery", "is_child": False},
            {"firstName": "Beatrice", "lastName": "Montgomery", "is_child": False},
            {"firstName": "Charles", "lastName": "Montgomery", "is_child": True},
            {"firstName": "Diana", "lastName": "Montgomery", "is_child": True},
            {"firstName": "Edward", "lastName": "Montgomery", "is_child": False},
        ],
        "booking_json": {
            "confirmation_code": "HTL-ROYAL-998877665544",
            "status": "confirmed",
        },
    }

    pdf_bytes = build_hotel_booking_pdf(record)
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 2000


def test_pdf_with_minimal_missing_fields():
    """Verify PDF generates cleanly with missing optional fields without raising exceptions."""
    minimal_record = {
        "id": "tx_minimal_1",
        "amount_inr": 12000.0,
    }

    pdf_bytes = build_hotel_booking_pdf(minimal_record)
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000
