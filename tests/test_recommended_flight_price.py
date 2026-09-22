from services.travel_planner import budget_from_live_data

def test_recommended_offer_is_used_for_budget_not_cheapest():
    offers = [
        {"id": "cheap", "price": 4000, "currency": "INR", "is_recommended": False},
        {"id": "rec", "price": 5500, "currency": "INR", "is_recommended": True},
    ]
    result = budget_from_live_data({"duration_days": 3, "passenger_count": 2}, offers, "")
    assert result["flight_estimate"] == 5500
    assert result["recommended_flight_id"] == "rec"
