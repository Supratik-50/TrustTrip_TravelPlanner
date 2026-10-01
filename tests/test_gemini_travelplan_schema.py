from services.chat_planner import (
    BudgetPlan,
    DayPlan,
    TravelPlan,
    _gemini_response_schema,
)
from google.genai import types


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def test_gemini_schema_removes_unsupported_additional_properties_recursively():
    schema = _gemini_response_schema()
    assert all("additionalProperties" not in node for node in _walk(schema))
    assert all("additional_properties" not in node for node in _walk(schema))

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
    )
    transmitted_schema = config.model_dump(exclude_none=True).get("response_schema")
    assert transmitted_schema is not None
    assert all("additional_properties" not in node for node in _walk(transmitted_schema))


def test_gemini_schema_preserves_required_travelplan_fields():
    original = TravelPlan.model_json_schema()
    converted = _gemini_response_schema()
    assert converted["required"] == original["required"]
    assert converted["properties"].keys() == original["properties"].keys()
    assert converted["properties"]["duration_days"]["type"] == "integer"
    assert converted["properties"]["budget"]["$ref"] == original["properties"]["budget"]["$ref"]


def test_gemini_schema_preserves_nested_budget_plan_structure():
    schema = _gemini_response_schema()
    budget = schema["$defs"]["BudgetPlan"]
    assert budget["type"] == "object"
    assert budget["properties"].keys() == BudgetPlan.model_json_schema()["properties"].keys()
    assert budget["required"] == BudgetPlan.model_json_schema()["required"]
    assert budget["properties"]["within_budget"]["type"] == "boolean"


def test_gemini_schema_preserves_daily_plan_array_and_day_fields():
    schema = _gemini_response_schema()
    daily_plan = schema["properties"]["daily_plan"]
    assert daily_plan["type"] == "array"
    assert daily_plan["items"]["$ref"] == "#/$defs/DayPlan"
    assert schema["$defs"]["DayPlan"]["properties"].keys() == DayPlan.model_json_schema()["properties"].keys()


def test_gemini_schema_transformation_is_deterministic():
    assert _gemini_response_schema() == _gemini_response_schema()


def test_travelplan_pydantic_validation_remains_available():
    plan = TravelPlan.model_validate(
        {
            "trip_summary": "Kolkata to Goa",
            "route": "Kolkata to Goa",
            "duration_days": 10,
            "travel_dates": "Oct 14 to Oct 24",
            "travel_style": "balanced",
            "daily_plan": [
                {
                    "day": 1,
                    "title": "Arrival",
                    "morning": "Travel",
                    "afternoon": "Check in",
                    "evening": "Rest",
                    "estimated_daily_cost_inr": 0,
                }
            ],
            "budget": {
                "flight_estimate_inr": 0,
                "hotel_estimate_inr": 0,
                "local_transport_estimate_inr": 0,
                "activities_estimate_inr": 0,
                "contingency_inr": 0,
                "estimated_total_inr": 0,
                "within_budget": True,
                "pricing_note": "No live offers supplied",
            },
            "flight_suggestion": "No live flight offer",
            "accommodation_plan": "No live hotel offer",
            "weather_guidance": "Check forecast before travel",
        }
    )
    assert plan.destination is None
    assert plan.daily_plan[0].day == 1
