"""Comprehensive regression test suite for global destination resolution safety.

Validates the priority order:
1. Exact IATA code
2. Exact known city name
3. Exact known alias
4. Exact state/region mapping where supported
5. Exact city/state + country mapping
6. Country-aware matching
7. Controlled safe fuzzy matching (never cross-border)
8. Safe failure on unknown/ambiguous destinations
"""
from datetime import date, timedelta
import pytest
from services.travel_planner import _resolve_place, CITY_TO_IATA
from services.flight_search_service import resolve_canonical_trip_criteria


def test_matrix_a_exact_iata():
    """Exact 3-letter IATA codes must resolve directly to that airport."""
    assert _resolve_place("CCU")[0] == "CCU"
    assert _resolve_place("COK")[0] == "COK"
    assert _resolve_place("CDG")[0] == "CDG"
    assert _resolve_place("LHR")[0] == "LHR"
    assert _resolve_place("JFK")[0] == "JFK"
    # In parentheses
    assert _resolve_place("Kolkata (CCU)")[0] == "CCU"
    assert _resolve_place("Kochi (COK)")[0] == "COK"
    assert _resolve_place("Paris (CDG)")[0] == "CDG"


def test_matrix_b_exact_cities():
    """Exact city names must resolve to their canonical primary airport."""
    assert _resolve_place("Kolkata")[0] == "CCU"
    assert _resolve_place("Kochi")[0] == "COK"
    assert _resolve_place("Paris")[0] == "CDG"
    assert _resolve_place("Tokyo")[0] == "HND"
    assert _resolve_place("Delhi")[0] == "DEL"
    assert _resolve_place("Mumbai")[0] == "BOM"


def test_matrix_c_country_aware_destinations():
    """Country-qualified inputs must respect their country context."""
    res_kerala = _resolve_place("Kerala, India")
    assert res_kerala[0] == "COK"
    assert res_kerala[2] == "India"

    res_goa = _resolve_place("Goa, India")
    assert res_goa[0] == "GOI"
    assert res_goa[2] == "India"

    res_paris = _resolve_place("Paris, France")
    assert res_paris[0] == "CDG"
    assert res_paris[2] == "France"

    res_tokyo = _resolve_place("Tokyo, Japan")
    assert res_tokyo[0] == "HND"
    assert res_tokyo[2] == "Japan"

    res_ny = _resolve_place("New York, USA")
    assert res_ny[0] in {"JFK", "EWR"}
    assert res_ny[2] == "United States"


def test_matrix_d_negative_foreign_fuzzy_collisions():
    """Critical safety test: Indian regions/cities must NEVER resolve to foreign airports.

    Kerala, India must NEVER resolve to KJP (Kerama Airport, Japan).
    """
    res = _resolve_place("Kerala, India")
    assert res[0] != "KJP", "Kerala, India MUST NEVER resolve to KJP (Kerama Airport, Japan)"
    assert res[0] == "COK", "Kerala, India should resolve to COK (Cochin International Airport)"
    assert res[2] == "India"

    # Region without country specified
    res_kerala_alone = _resolve_place("Kerala")
    assert res_kerala_alone[0] != "KJP"
    assert res_kerala_alone[0] == "COK"
    assert res_kerala_alone[2] == "India"

    # Random place in India must not jump to an overseas airport
    res_gibberish = _resolve_place("RandomGibberishPlace, India")
    assert res_gibberish[0] is None, "Unrecognized place in India must not invent an overseas airport"
    assert res_gibberish[2] == "India"


def test_matrix_e_existing_aliases_preserved():
    """All existing CITY_TO_IATA aliases must continue working."""
    for city, expected_code in list(CITY_TO_IATA.items())[:30]:
        resolved_code, _, _ = _resolve_place(city)
        assert resolved_code is not None, f"Failed to resolve alias '{city}'"
        assert resolved_code == expected_code, f"Mismatch for '{city}': got {resolved_code}, expected {expected_code}"


def test_matrix_f_explicit_city_beats_region():
    """When a specific city within a region is provided, the city's airport must be chosen."""
    # Kochi in Kerala -> COK
    assert _resolve_place("Kochi, India")[0] == "COK"
    assert _resolve_place("Cochin, India")[0] == "COK"
    # Trivandrum / Thiruvananthapuram in Kerala -> TRV (not generic COK)
    assert _resolve_place("Thiruvananthapuram, India")[0] == "TRV"
    assert _resolve_place("Trivandrum, India")[0] == "TRV"
    # Calicut / Kozhikode in Kerala -> CCJ
    assert _resolve_place("Kozhikode, India")[0] == "CCJ"
    assert _resolve_place("Calicut, India")[0] == "CCJ"
    # Kannur in Kerala -> CNN
    assert _resolve_place("Kannur, India")[0] == "CNN"


def test_matrix_g_explicit_iata_beats_everything():
    """Explicit IATA codes must be strictly preserved."""
    assert _resolve_place("COK")[0] == "COK"
    assert _resolve_place("KJP")[0] == "KJP"
    assert _resolve_place("CCU")[0] == "CCU"
    assert _resolve_place("TRV")[0] == "TRV"


def test_matrix_h_unknown_ambiguous_fails_safely():
    """Unresolvable destinations must fail safely without inventing bogus airports."""
    assert _resolve_place("XyzzzNonExistentCity123")[0] is None
    assert _resolve_place("XyzzzNonExistentCity123, India")[0] is None
    assert _resolve_place("SomeFakePlace, France")[0] is None
    assert _resolve_place("")[0] is None
    # Country-only does not invent an airport
    assert _resolve_place("India")[0] is None
    assert _resolve_place("France")[0] is None
    assert _resolve_place("Japan")[0] is None


def test_matrix_i_bidirectional_return_route_consistency():
    """Canonical criteria resolution must produce symmetric airport pairs for outbound and return."""
    sample_trip = {
        "id": "test_trip_123",
        "origin": "Kolkata (CCU)",
        "destination": "Kerala, India",
        "metadata": {
            "origin": "Kolkata (CCU)",
            "destination": "Kerala, India",
            "start_date": (date.today() + timedelta(days=14)).isoformat(),
            "end_date": (date.today() + timedelta(days=21)).isoformat(),
            "travelers_count": 2,
        }
    }
    criteria = resolve_canonical_trip_criteria(sample_trip)
    assert criteria["origin_iata"] == "CCU"
    assert criteria["destination_iata"] == "COK"
    assert criteria["destination_iata"] != "KJP"


def test_matrix_j_planner_and_flight_search_destination_parity():
    """Ensure destination resolved during trip planning matches criteria in flight search."""
    origin_code, _, _ = _resolve_place("Kolkata (CCU)")
    dest_code, _, _ = _resolve_place("Kerala, India")

    assert origin_code == "CCU"
    assert dest_code == "COK"

    trip_mock = {
        "id": "parity_test_trip",
        "origin": "Kolkata (CCU)",
        "destination": "Kerala, India",
        "metadata": {
            "origin": "Kolkata (CCU)",
            "destination": "Kerala, India",
            "constraints": {
                "origin_airport": origin_code,
                "destination_airport": dest_code,
            },
            "start_date": (date.today() + timedelta(days=14)).isoformat(),
            "end_date": (date.today() + timedelta(days=21)).isoformat(),
        }
    }
    criteria = resolve_canonical_trip_criteria(trip_mock)
    assert criteria["origin_iata"] == origin_code == "CCU"
    assert criteria["destination_iata"] == dest_code == "COK"
