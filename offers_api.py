"""API Router for Live Travel Offers and Revalidations."""
from __future__ import annotations

from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dependencies.auth import get_current_user
from domain.live_offer import RevalidationResult
from domain.user import UserResponse
from services.planner_service import PlannerService

router = APIRouter(prefix="/api/offers", tags=["offers"])


class RevalidateOfferRequest(BaseModel):
    snapshot_id: UUID


@router.post("/revalidate", response_model=RevalidationResult)
async def revalidate_offer(
    request: RevalidateOfferRequest,
    current_user: UserResponse = Depends(get_current_user),
):
    """Revalidate an immutable offer snapshot by snapshot_id.

    Rejects provider_offer_id or unauthenticated / cross-tenant calls.
    """
    try:
        return await PlannerService().revalidate_offer(
            snapshot_id=request.snapshot_id,
            authenticated_user_id=current_user.id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Offer revalidation failed: {exc}")
