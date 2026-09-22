import os
from services.hotel.hotel_service import HotelService

class FakeProvider:
    name="fake"

def test_hotel_ranking_prefers_budget_and_rating():
    svc=HotelService()
    offers=[
        {"id":"a","price":300,"currency":"USD","rating":9.0,"refundable":True,"board_name":"Breakfast","provider":"x"},
        {"id":"b","price":100,"currency":"USD","rating":7.0,"refundable":False,"board_name":"Room Only","provider":"x"},
    ]
    svc._providers=lambda: []
    svc.rate_cache={"USD":{"rate":85.0,"date":"2026-09-18"}}
    for o in offers: svc._inr(o)
    assert all(o.get("price_inr") for o in offers)
    assert offers[0]["price_inr"] > offers[1]["price_inr"]

def test_hotel_provider_config_can_be_empty_without_crashing():
    old1=os.environ.pop("LITEAPI_SANDBOX_API_KEY",None)
    old2=os.environ.pop("LITEAPI_PRODUCTION_API_KEY",None)
    old3=os.environ.pop("HOTELBEDS_API_KEY",None)
    old4=os.environ.pop("HOTELBEDS_SECRET",None)
    try:
        result=HotelService().search({"destination":"Goa","checkin":"2026-10-25","checkout":"2026-10-29","adults":2,"children":0,"rooms":1})
        assert result["offers"] == []
        assert result["errors"]
    finally:
        if old1 is not None: os.environ["LITEAPI_SANDBOX_API_KEY"]=old1
        if old2 is not None: os.environ["LITEAPI_PRODUCTION_API_KEY"]=old2
        if old3 is not None: os.environ["HOTELBEDS_API_KEY"]=old3
        if old4 is not None: os.environ["HOTELBEDS_SECRET"]=old4
