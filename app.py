from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import traceback

from dotenv import load_dotenv
load_dotenv()

# Configure stdlib logging before other imports
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger(__name__)

import uvicorn
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from backend import run_travel_agent, resume_travel_agent
from auth_api import router as auth_router
from flight_api import router as flight_router
from payment_api import router as payment_router
from trips_api import router as trips_router
from hotel_api import router as hotel_router
from hotel_payment_api import router as hotel_payment_router
from offers_api import router as offers_router
from research_api import router as research_router
from flight_booking_api import router as flight_booking_router
from services.travel_plan_pdf import build_travel_plan_pdf
from dependencies.auth import get_current_user
from domain.user import UserResponse

# This is kept from the original project to allow the existing synchronous
# agent functions to call async MCP helpers inside FastAPI.
import nest_asyncio

nest_asyncio.apply()

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    from services.config import validate_config
    from services.db import probe_db, init_db, close_db
    from services.auth_store import init_auth_tables
    from services.trip_store import init_trip_tables
    from services.search_session_store import validate_database_schema

    logger.info("Application starting up...")
    validate_config()
    init_db()

    # In persistent DB mode, verify connectivity at startup
    if os.getenv("USE_IN_MEMORY_AUTH", "").lower() not in {"1", "true", "yes"}:
        if not probe_db():
            raise RuntimeError("Database connectivity check failed during startup.")

    init_auth_tables()
    init_trip_tables()
    validate_database_schema()
    logger.info("Application ready")

    yield

    logger.info("Application shutting down...")
    close_db()
    logger.info("Application shutdown complete")


app = FastAPI(
    title="Trust Trip MAS",
    description=(
        "LangGraph Multi-Agent Travel Planner with Supervisor, Guardrails, "
        "Human-in-the-Loop, and FastAPI Frontend"
    ),
    version="2.0.0",
    lifespan=lifespan,
)

# CORS Configuration
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:8000")
allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]
has_wildcard = "*" in allowed_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=not has_wildcard,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": "An internal server error occurred."},
    )


app.include_router(auth_router)
app.include_router(flight_router)
app.include_router(payment_router)
app.include_router(trips_router)
app.include_router(hotel_router)
app.include_router(hotel_payment_router)
app.include_router(offers_router)
app.include_router(research_router)
app.include_router(flight_booking_router)

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class TravelRequest(BaseModel):
    message: str
    thread_id: str | None = None
    trip_id: str | None = None


class TravelPlanPDFRequest(BaseModel):
    plan: dict


class ApprovalRequest(BaseModel):
    thread_id: str = Field(min_length=1)
    approved: bool
    feedback: str = ""


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )


@app.post("/api/travel")
async def travel_planner(
    request_data: TravelRequest,
    current_user: UserResponse = Depends(get_current_user),
):
    try:
        user_message = request_data.message.strip()

        if not user_message:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "Message cannot be empty.",
                },
            )

        # Identify target canonical trip if available
        target_trip_id = request_data.trip_id
        if not target_trip_id and request_data.thread_id:
            try:
                from services.trip_store import TripStore
                th = TripStore().get_thread(request_data.thread_id, current_user.id)
                if th and th.get("trip_id"):
                    target_trip_id = str(th["trip_id"])
            except Exception:
                pass

        result = run_travel_agent(
            user_input=user_message,
            thread_id=request_data.thread_id,
            authenticated_user_id=str(current_user.id),
            trip_id=target_trip_id,
        )

        # If a canonical trip is bound, persist generated plan into trip metadata
        if target_trip_id and (result.get("success") is not False):
            try:
                from services.trip_store import TripStore
                from fastapi.encoders import jsonable_encoder
                ts = TripStore()
                trip_record = ts.get_trip(target_trip_id, current_user.id)
                logger.info("Persisting plan for trip %s, found=%s", target_trip_id, bool(trip_record))
                if trip_record:
                    meta = trip_record.get("metadata") or {}
                    if isinstance(meta, str):
                        import json
                        meta = json.loads(meta)
                    meta = dict(meta)
                    if result.get("plan_json"):
                        meta["plan"] = jsonable_encoder(result.get("plan_json"))
                    if result.get("answer") or result.get("itinerary"):
                        meta["raw_plan"] = str(result.get("answer") or result.get("itinerary"))
                    assigned_thread = result.get("thread_id") or request_data.thread_id
                    if assigned_thread:
                        meta["thread_id"] = str(assigned_thread)
                    updated = ts.update_trip(target_trip_id, current_user.id, metadata=meta, status="planning")
                    logger.info("Trip %s updated with plan: %s", target_trip_id, bool(updated))
            except Exception as store_err:
                logger.error("Failed to persist travel plan to trip metadata: %s", store_err, exc_info=True)

        from fastapi.encoders import jsonable_encoder
        out_dict = dict(result)
        out_dict["success"] = True
        return JSONResponse(
            content=jsonable_encoder(out_dict)
        )

    except PermissionError as p_exc:
        raise HTTPException(status_code=403, detail=str(p_exc))
    except ValueError as v_exc:
        logger.error("Validation error in travel planner: %s", v_exc)
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error": str(v_exc),
            },
        )
    except Exception as exc:
        logger.exception("Error processing travel request: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "Travel planner processing failed.",
            },
        )


@app.post("/api/travel/pdf")
async def download_travel_plan_pdf(request_data: TravelPlanPDFRequest):
    try:
        pdf = build_travel_plan_pdf(request_data.plan)
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="trust-trip-travel-plan.pdf"'},
        )
    except Exception as exc:
        logger.exception("Travel plan PDF generation failed: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Travel plan PDF generation failed."},
        )


@app.post("/api/travel/approve")
async def approve_travel_plan(
    request_data: ApprovalRequest,
    current_user: UserResponse = Depends(get_current_user),
):
    try:
        if not request_data.approved and not request_data.feedback.strip():
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "Please provide revision feedback when rejecting the draft.",
                },
            )

        result = resume_travel_agent(
            thread_id=request_data.thread_id,
            approved=request_data.approved,
            feedback=request_data.feedback,
            authenticated_user_id=str(current_user.id),
        )

        return JSONResponse(
            content={
                "success": True,
                **result,
            }
        )

    except PermissionError as p_exc:
        raise HTTPException(status_code=403, detail=str(p_exc))
    except Exception as exc:
        logger.exception("Error approving travel plan: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "Travel plan approval processing failed.",
            },
        )


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "version": "2.0.0",
        "message": "Trust Trip MAS API is running",
        "features": [
            "supervisor_agent",
            "input_guardrail",
            "human_in_the_loop",
            "flight_search",
            "flight_recheck",
            "human_confirmed_flight_booking",
            "provider_agnostic_flight_service",
            "real_time_adaptive_replanning",
            "razorpay_payment_adapter",
            "payment_signature_verification",
            "payment_webhook_endpoint",
            "transaction_state_machine",
            "my_trips",
            "cancellation_and_refund_workflow",
            "dual_provider_hotel_search",
            "hotel_prebook_and_razorpay_booking",
            "postgres_connection_pool",
        ],
    }


@app.get("/ready")
async def readiness_check():
    from services.db import probe_db

    if probe_db():
        return {"status": "ready"}
    return JSONResponse(
        status_code=503,
        content={"status": "not_ready", "reason": "database_unavailable"},
    )


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
