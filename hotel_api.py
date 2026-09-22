from __future__ import annotations
import os, uuid
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from services.hotel.hotel_service import HotelService
from services.hotel.hotel_provider import HotelProviderError

router = APIRouter(prefix="/api/hotels", tags=["hotels"])

class HotelSearchRequest(BaseModel):
    destination: str = Field(min_length=2)
    checkin: str
    checkout: str
    adults: int = Field(default=1, ge=1, le=12)
    children: int = Field(default=0, ge=0, le=12)
    rooms: int = Field(default=1, ge=1, le=8)
    budget_inr: float | None = Field(default=None, gt=0)
    cheaper_mode: bool = False
    hotelbeds_destination_code: str | None = None

class HotelPrebookRequest(BaseModel):
    offer: dict[str, Any]

@router.get("/config")
async def hotel_config():
    return {
        "success": True,
        "providers": {
            "liteapi": bool(os.getenv("LITEAPI_SANDBOX_API_KEY") or os.getenv("LITEAPI_PRODUCTION_API_KEY")),
            "hotelbeds": bool(os.getenv("HOTELBEDS_API_KEY") and os.getenv("HOTELBEDS_SECRET")),
        },
        "liteapi_mode": os.getenv("LITEAPI_MODE", "sandbox"),
        "hotelbeds_mode": os.getenv("HOTELBEDS_MODE", "test"),
        "razorpay_configured": bool(os.getenv("RAZORPAY_KEY_ID") and os.getenv("RAZORPAY_KEY_SECRET")),
    }

@router.post("/search")
async def search_hotels(req: HotelSearchRequest):
    try:
        result = HotelService().search(req.model_dump())
        return {"success": True, **result}
    except HotelProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Hotel search failed: {exc}")

@router.post("/prebook")
async def prebook_hotel(req: HotelPrebookRequest):
    offer=req.offer
    if not offer.get("provider_offer_id") or offer.get("provider") not in {"liteapi", "hotelbeds"}:
        raise HTTPException(status_code=422, detail="Invalid hotel offer.")
    try:
        result=HotelService().prebook(offer)
        return {"success": True, "prebook": result}
    except HotelProviderError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
