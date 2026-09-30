import pytest
import uuid
from unittest.mock import patch
from services.planner_service import PlannerService

def _canonical(trip_id, user_id, **overrides):
    base = {
        'id': trip_id, 'user_id': user_id,
        'destination': 'Goa', 'origin': 'Kolkata',
        'start_date': '2026-10-14', 'end_date': '2026-10-24',
        'duration_days': 11, 'travelers_count': 2,
        'budget': 45000.0, 'currency': 'INR',
        'current_revision_number': 0,
        'metadata': {
            'origin': 'CCU', 'destination': 'GOI',
            'origin_city': 'Kolkata', 'destination_city': 'Goa',
            'departure_date': '2026-10-14', 'return_date': '2026-10-24',
            'duration_days': 11, 'adults': 2,
            'budget_amount': 45000.0, 'currency': 'INR',
        },
    }
    base.update(overrides)
    return base

def _full_plan(**overrides):
    p = {
        'destination': 'Goa', 'destination_airport': 'GOI',
        'origin': 'Kolkata', 'origin_airport': 'CCU',
        'departure_date': '2026-10-14', 'return_date': '2026-10-24',
        'duration_days': 11, 'travelers': 2,
        'budget': {'target_budget': 45000.0, 'target_budget_inr': 45000.0, 'currency': 'INR'},
    }
    p.update(overrides)
    return p

def _run(svc, user_id, trip_id, plan_json, canonical_override=None):
    canonical = canonical_override if canonical_override is not None else _canonical(trip_id, user_id)
    thread = {'id': 'th-' + str(uuid.uuid4()), 'trip_id': trip_id, 'user_id': user_id}
    g = {'messages': [], 'guardrail_allowed': True, 'planning_blocked': False, 'plan_json': plan_json}
    with patch('services.trip_store.TripStore.get_trip', return_value=canonical):
        with patch('services.trip_store.TripStore.create_thread', return_value=thread):
            with patch('backend.travel_graph.invoke', return_value=g):
                return svc.plan_travel(user_input='Update', authenticated_user_id=user_id, trip_id=trip_id)

def _reject(svc, user_id, trip_id, plan_json, fragment, canonical_override=None):
    canonical = canonical_override if canonical_override is not None else _canonical(trip_id, user_id)
    thread = {'id': 'th-' + str(uuid.uuid4()), 'trip_id': trip_id, 'user_id': user_id}
    g = {'messages': [], 'guardrail_allowed': True, 'planning_blocked': False, 'plan_json': plan_json}
    with patch('services.trip_store.TripStore.get_trip', return_value=canonical):
        with patch('services.trip_store.TripStore.create_thread', return_value=thread):
            with patch('backend.travel_graph.invoke', return_value=g):
                with pytest.raises(ValueError) as exc:
                    svc.plan_travel(user_input='Update', authenticated_user_id=user_id, trip_id=trip_id)
    assert fragment.lower() in str(exc.value).lower(), f'Expected {fragment!r} in: {exc.value}'

@pytest.fixture
def svc():
    return PlannerService()

@pytest.fixture
def ids():
    return str(uuid.uuid4()), str(uuid.uuid4())

# Defect 1
def test_missing_origin_rejected(svc, ids):
    user_id, trip_id = ids
    plan = _full_plan()
    del plan['origin']
    del plan['origin_airport']
    _reject(svc, user_id, trip_id, plan, 'missing required origin')

def test_wrong_origin_rejected(svc, ids):
    user_id, trip_id = ids
    plan = _full_plan()
    plan['origin'] = 'Delhi'
    plan['origin_airport'] = 'DEL'
    _reject(svc, user_id, trip_id, plan, 'does not match canonical trip origin')

def test_correct_origin_passes(svc, ids):
    user_id, trip_id = ids
    assert isinstance(_run(svc, user_id, trip_id, _full_plan()), dict)

# Defect 2
def test_missing_destination_rejected(svc, ids):
    user_id, trip_id = ids
    plan = _full_plan()
    del plan['destination']
    del plan['destination_airport']
    _reject(svc, user_id, trip_id, plan, 'missing required destination')

def test_wrong_destination_rejected(svc, ids):
    user_id, trip_id = ids
    plan = _full_plan()
    plan['destination'] = 'Mumbai'
    plan['destination_airport'] = 'BOM'
    _reject(svc, user_id, trip_id, plan, 'does not match canonical trip destination')

def test_correct_destination_passes(svc, ids):
    user_id, trip_id = ids
    assert isinstance(_run(svc, user_id, trip_id, _full_plan()), dict)

# Defect 3
def test_zero_budget_missing_plan_budget_rejects(svc, ids):
    user_id, trip_id = ids
    canonical = _canonical(trip_id, user_id, budget=0)
    canonical['metadata'] = dict(canonical['metadata'])
    canonical['metadata']['budget_amount'] = 0
    plan = _full_plan()
    del plan['budget']
    _reject(svc, user_id, trip_id, plan, 'missing required target_budget', canonical_override=canonical)

def test_zero_budget_matching_plan_passes(svc, ids):
    user_id, trip_id = ids
    canonical = _canonical(trip_id, user_id, budget=0)
    canonical['metadata'] = dict(canonical['metadata'])
    canonical['metadata']['budget_amount'] = 0
    plan = _full_plan()
    plan['budget'] = {'target_budget': 0, 'target_budget_inr': 0, 'currency': 'INR'}
    assert isinstance(_run(svc, user_id, trip_id, plan, canonical_override=canonical), dict)

def test_zero_budget_wrong_plan_budget_rejects(svc, ids):
    user_id, trip_id = ids
    canonical = _canonical(trip_id, user_id, budget=0)
    canonical['metadata'] = dict(canonical['metadata'])
    canonical['metadata']['budget_amount'] = 0
    plan = _full_plan()
    plan['budget'] = {'target_budget': 1000.0, 'target_budget_inr': 1000.0, 'currency': 'INR'}
    _reject(svc, user_id, trip_id, plan, 'does not match canonical target budget', canonical_override=canonical)

def test_none_budget_is_not_applicable(svc, ids):
    user_id, trip_id = ids
    canonical = _canonical(trip_id, user_id, budget=None)
    canonical['metadata'] = dict(canonical['metadata'])
    canonical['metadata'].pop('budget_amount', None)
    plan = _full_plan()
    plan['budget'] = {'currency': 'INR'}
    assert isinstance(_run(svc, user_id, trip_id, plan, canonical_override=canonical), dict)
