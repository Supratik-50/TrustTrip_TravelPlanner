from services.travel_planner import parse_trip_request, candidate_dates
from services.travel_plan_pdf import build_travel_plan_pdf
from services.chat_planner import TravelPlan, BudgetPlan, DayPlan


def test_world_route_and_explicit_dates_are_parsed_without_llm():
    c = parse_trip_request("Plan a trip from Kolkata to Ahmedabad on 5 October 2026 to 10 October 2026 for 2 adults under ₹60000")
    assert c["origin"] == "CCU"
    assert c["destination"] == "AMD"
    assert c["departure_date"] == "2026-10-05"
    assert c["return_date"] == "2026-10-10"
    assert c["duration_days"] == 6
    assert candidate_dates(c, 1) == [("2026-10-05", "2026-10-10")]


def test_surats_route_is_supported_without_llm():
    c = parse_trip_request("Plan a trip from Kolkata to Surat on 2026-10-05 to 2026-10-08 for 2 travellers")
    assert c["origin"] == "CCU"
    assert c["destination"] == "STV"
    assert c["destination_city"] == "Surat"


def test_server_pdf_contains_real_pdf_content():
    plan = TravelPlan(
        trip_summary="Test travel plan", route="Kolkata → Surat", duration_days=4,
        travel_dates="2026-10-05 to 2026-10-08", travel_style="Balanced",
        daily_plan=[DayPlan(day=1, title="Arrival", morning="Explore", afternoon="Lunch", evening="Relax", estimated_daily_cost_inr=1000)],
        budget=BudgetPlan(currency="INR", flight_estimate_inr=10000, hotel_estimate_inr=12000, local_transport_estimate_inr=3000, activities_estimate_inr=3000, contingency_inr=2800, estimated_total_inr=30800, target_budget_inr=60000, within_budget=True, pricing_note="Provider-backed where available"),
        flight_suggestion="**Recommended flight:** **Duffel Airways** for **USD 120** (₹10,000 INR).",
        accommodation_plan="**Recommended hotel:** **Test Hotel** for **₹12,000 total**.",
        weather_guidance="**Surat**: **30°C**, **70% humidity**.",
    )
    pdf = build_travel_plan_pdf(plan.model_dump())
    assert pdf.startswith(b"%PDF")
    assert b"%%EOF" in pdf
    assert len(pdf) > 1000
    assert b"/Catalog" in pdf or b"/Root" in pdf


def test_universal_domestic_and_international_routes_are_not_hardcoded():
    domestic = parse_trip_request("Plan a trip from Chennai to Kolkata on 2026-11-05 for 2 travellers")
    assert domestic["origin"] == "MAA"
    assert domestic["destination"] == "CCU"
    assert domestic["origin_country"] == "India"
    assert domestic["destination_country"] == "India"
    assert domestic["journey_scope"] == "domestic"

    international = parse_trip_request("Plan a trip from New York to London on 2026-12-05")
    assert international["origin"] == "JFK"
    assert international["destination"] == "LHR"
    assert international["journey_scope"] == "international"


def test_country_only_endpoint_is_never_sent_as_an_iata_code():
    request = parse_trip_request("Plan a trip from India to Japan on 2026-12-05")
    assert request["origin"] is None
    assert request["destination"] is None
    assert request["origin_country"] == "India"
    assert request["destination_country"] == "Japan"
    assert request["origin_query"] == "india"
    assert request["destination_query"] == "japan"


def test_country_only_endpoints_regression():
    req1 = parse_trip_request("Plan a trip from India to Netherlands on 2026-12-05")
    assert req1["origin"] is None
    assert req1["destination"] is None
    assert req1["origin_country"] == "India"
    assert req1["destination_country"] == "Netherlands"

    req2 = parse_trip_request("Plan a trip from Netherlands to Japan on 2026-12-05")
    assert req2["origin"] is None
    assert req2["destination"] is None
    assert req2["origin_country"] == "Netherlands"
    assert req2["destination_country"] == "Japan"



def test_preset_style_trip_keeps_explicit_departure_city():
    request = parse_trip_request("Plan a 5 days Dubai trip from Chennai with flights and hotels")
    assert request["origin"] == "MAA"
    assert request["destination"] == "DXB"
    assert request["origin_city"] == "Chennai"
    assert request["destination_city"] == "Dubai"
    assert request["journey_scope"] == "international"


def test_new_trip_does_not_inherit_previous_route():
    previous = parse_trip_request("Mumbai to London on 2026-10-05")
    fresh = parse_trip_request("Japan trip", previous)
    assert fresh["origin"] is None
    assert fresh["destination"] == "HND"
    assert fresh["destination_city"] == "Tokyo"
    assert fresh["destination_country"] == "Japan"


def test_quick_trip_presets_default_to_one_traveller():
    for query, code, city in [
        ("Japan trip", "HND", "Tokyo"),
        ("Dubai trip", "DXB", "Dubai"),
        ("Thailand trip", "BKK", "Bangkok"),
    ]:
        c = parse_trip_request(query)
        assert c["destination"] == code
        assert c["destination_city"] == city
        assert c["adults"] == 1
        assert c["passenger_count"] == 1


def test_city_to_city_route_defaults_to_one_traveller_without_date():
    c = parse_trip_request("Kolkata to Tokyo")
    assert c["origin"] == "CCU"
    assert c["destination"] == "HND"
    assert c["departure_date"] is None
    assert c["passenger_count"] == 1
