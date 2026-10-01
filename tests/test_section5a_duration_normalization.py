"""Focused duration normalization regressions for Section 5A."""
from uuid import UUID

from services.flight_search_service import normalize_provider_offer


def _segment(origin, destination, departure, arrival, duration):
    return {
        "origin": origin,
        "destination": destination,
        "departure": departure,
        "arrival": arrival,
        "duration": duration,
        "marketing_carrier": "Example Air",
        "marketing_carrier_iata": "EX",
        "flight_number": "EX 1",
    }


def test_single_segment_duration_and_provider_id_shielding():
    first = _segment("CCU", "DEL", "2026-10-14T06:00:00Z", "2026-10-14T08:30:00Z", "PT2H30M")
    offer, _, raw_id = normalize_provider_offer(
        {"id": "off_private_single", "segments": [first], "raw": {"slices": [{"duration": "PT2H30M", "segments": [first]}]}, "price": 100, "currency": "INR"},
        "CCU", "DEL", 1,
    )
    assert offer.duration == "2h 30m"
    assert offer.segments[0].duration == "2h 30m"
    assert raw_id == "off_private_single"
    assert str(UUID(offer.offer_id)) == offer.offer_id
    assert raw_id not in offer.model_dump_json()


def test_multisegment_offer_duration_covers_whole_outbound_itinerary():
    first = _segment("CCU", "BOM", "2026-10-14T06:00:00Z", "2026-10-14T08:00:00Z", "PT2H")
    second = _segment("BOM", "DEL", "2026-10-14T09:00:00Z", "2026-10-14T11:30:00Z", "PT2H30M")
    offer, _, _ = normalize_provider_offer(
        {
            "id": "off_private_connection", "segments": [first, second], "price": 100, "currency": "INR",
            "raw": {"slices": [{"duration": "PT5H30M", "segments": [
                {"departing_at": first["departure"], "arriving_at": first["arrival"]},
                {"departing_at": second["departure"], "arriving_at": second["arrival"]},
            ]}]},
        },
        "CCU", "DEL", 1,
    )
    assert offer.duration == "5h 30m"
    assert [segment.duration for segment in offer.segments] == ["2h", "2h 30m"]


def test_segment_duration_falls_back_to_actual_timestamps():
    segment = _segment("CCU", "DEL", "2026-10-14T06:00:00Z", "2026-10-14T08:45:00Z", None)
    offer, _, _ = normalize_provider_offer(
        {"id": "off_private_timestamp", "segments": [segment], "price": 100, "currency": "INR", "raw": {"slices": []}},
        "CCU", "DEL", 1,
    )
    assert offer.duration == "2h 45m"
    assert offer.segments[0].duration == "2h 45m"
