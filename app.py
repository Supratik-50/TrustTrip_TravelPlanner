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
from dependencies.auth import get_current_user, get_optional_current_user
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
    from services.migration_runner import run_migrations
    run_migrations()
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
_frontend_env = os.getenv("FRONTEND_API_URL") or os.getenv("FRONTEND_URL")
if _frontend_env and _frontend_env.strip() not in allowed_origins:
    allowed_origins.append(_frontend_env.strip())
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
    plan: dict | None = None
    trip_id: str | None = None


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
        if request_data.thread_id:
            from services.trip_store import TripStore
            ts = TripStore()
            th = ts.get_thread(request_data.thread_id, current_user.id)
            if not th:
                raise HTTPException(
                    status_code=403,
                    detail=f"Thread '{request_data.thread_id}' not found or does not belong to the user.",
                )
            thread_trip_id = th.get("trip_id")
            if target_trip_id and thread_trip_id and str(target_trip_id) != str(thread_trip_id):
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "error": f"Trip ID mismatch: thread bound to trip {thread_trip_id} but request specified trip {target_trip_id}",
                    },
                )
            if not target_trip_id and thread_trip_id:
                target_trip_id = str(thread_trip_id)

        if target_trip_id:
            from services.trip_store import TripStore
            ts = TripStore()
            trip_rec = ts.get_trip(target_trip_id, current_user.id)
            if not trip_rec:
                raise HTTPException(
                    status_code=403,
                    detail=f"Trip '{target_trip_id}' not found or access denied.",
                )

        result = run_travel_agent(
            user_input=user_message,
            thread_id=request_data.thread_id,
            authenticated_user_id=str(current_user.id),
            trip_id=target_trip_id,
        )

        # Determine actual success
        planner_success = result.get("success")
        if planner_success is None:
            if result.get("guardrail_allowed") is False or result.get("planning_blocked") is True or result.get("error"):
                planner_success = False
            else:
                planner_success = True

        from fastapi.encoders import jsonable_encoder
        out_dict = dict(result)

        if not planner_success:
            err_msg = result.get("error") or result.get("guardrail_reason") or "Travel planning failed."
            outage_code = result.get("status_code") or result.get("outage_status_code")
            if not outage_code:
                outage_code = 422 if result.get("guardrail_allowed") is False else 500

            if outage_code == 503 or (result.get("planning_blocked") and result.get("outage_status_code") == 503):
                out_dict["success"] = False
                out_dict["error"] = "Upstream AI travel planning service is temporarily overloaded. Please try again shortly."
                out_dict["retryable"] = True
                out_dict["status_code"] = 503
                return JSONResponse(
                    status_code=503,
                    content=jsonable_encoder(out_dict),
                    headers={"Retry-After": "5"},
                )

            if outage_code == 429 or (result.get("planning_blocked") and result.get("outage_status_code") == 429):
                out_dict["success"] = False
                out_dict["error"] = "AI planning rate limit exceeded. Please wait before retrying."
                out_dict["retryable"] = False
                out_dict["status_code"] = 429
                return JSONResponse(
                    status_code=429,
                    content=jsonable_encoder(out_dict),
                )

            out_dict["success"] = False
            out_dict["error"] = err_msg
            return JSONResponse(
                status_code=outage_code,
                content=jsonable_encoder(out_dict)
            )

        # If a canonical trip is bound and planner succeeded, persist generated plan into trip metadata.
        # Persistence is REQUIRED when a canonical trip_id is present.
        # A failure here must NOT be silently swallowed — the caller cannot distinguish
        # "generated and persisted" from "generated only" if we return success=True on error.
        if target_trip_id:
            from services.trip_store import TripStore
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
                try:
                    updated = ts.update_trip(target_trip_id, current_user.id, metadata=meta, status="planning")
                    logger.info("Trip %s updated with plan: %s", target_trip_id, bool(updated))
                    if not updated:
                        raise RuntimeError(
                            f"Trip metadata persistence returned no record for trip {target_trip_id}."
                        )
                except RuntimeError:
                    raise
                except Exception as store_err:
                    raise RuntimeError(
                        f"Failed to persist travel plan to trip {target_trip_id}: {store_err}"
                    ) from store_err

                # ------------------------------------------------------------------
                # Wave 1A — Canonical airport identity (Part F)
                #
                # Store destination_airport and origin_airport IATA codes into the
                # trip metadata WITHOUT changing trip.destination / trip.origin text.
                #
                # Resolution priority:
                #   1. plan_json["destination_airport"] / ["origin_airport"]
                #      (structured output from the chat planner — most authoritative)
                #   2. _resolve_place(trip.destination) / _resolve_place(trip.origin)
                #      (deterministic catalog resolver — fallback only)
                #
                # "Darjeeling, India" resolves via _resolve_place to the gateway
                # airport (IXB) which is then stored in metadata["destination_airport"].
                # The flight search resolver (resolve_canonical_trip_criteria) already
                # reads meta["destination_airport"] as its second-highest priority.
                # ------------------------------------------------------------------
                from services.travel_planner import _resolve_place as _rp

                plan_json_out = result.get("plan_json") or {}

                # Destination airport
                dest_iata: str | None = None
                _plan_da = str(plan_json_out.get("destination_airport") or "").strip().upper()
                if len(_plan_da) == 3 and _plan_da.isalpha():
                    dest_iata = _plan_da
                if not dest_iata:
                    _dest_raw = trip_record.get("destination") or ""
                    if _dest_raw:
                        _rc, _, _ = _rp(_dest_raw)
                        if _rc and len(_rc) == 3 and _rc.isalpha():
                            dest_iata = _rc.upper()

                # Origin airport
                orig_iata: str | None = None
                _plan_oa = str(plan_json_out.get("origin_airport") or "").strip().upper()
                if len(_plan_oa) == 3 and _plan_oa.isalpha():
                    orig_iata = _plan_oa
                if not orig_iata:
                    _orig_raw = trip_record.get("origin") or ""
                    if _orig_raw:
                        _ro, _, _ = _rp(_orig_raw)
                        if _ro and len(_ro) == 3 and _ro.isalpha():
                            orig_iata = _ro.upper()

                if dest_iata or orig_iata:
                    _fresh = ts.get_trip(target_trip_id, current_user.id)
                    if _fresh:
                        _fm = _fresh.get("metadata") or {}
                        if isinstance(_fm, str):
                            import json as _j
                            _fm = _j.loads(_fm)
                        _fm = dict(_fm)
                        if dest_iata:
                            _fm["destination_airport"] = dest_iata
                        if orig_iata:
                            _fm["origin_airport"] = orig_iata
                        ts.update_trip(target_trip_id, current_user.id, metadata=_fm)
                        logger.info(
                            "Wave1A: trip %s airports stored dest=%s orig=%s",
                            target_trip_id, dest_iata, orig_iata,
                        )

                # ------------------------------------------------------------------
                # Wave 1B — Create canonical PROPOSED revision (Parts A, B, C, D)
                #
                # CONTRACT:
                #   - Every successfully generated plan MUST produce a real revision
                #     row in plan_revisions (EvidenceRevisionStore).
                #   - Status is PROPOSED (never auto-approved).
                #   - First plan (current_revision_number == 0) → Revision #1 PROPOSED.
                #   - Re-plan (current_revision_number == N) → Revision N+1 PROPOSED.
                #   - Uses create_revision_atomic: row-locked, 409 on stale conflict,
                #     no duplicate on retry (caller must not retry on success).
                #   - Failure here is a hard error — the frontend cannot approve or
                #     book without a real revision_id.
                # ------------------------------------------------------------------
                try:
                    _trip_now = ts.get_trip(target_trip_id, current_user.id)
                    _cur_rev = int((_trip_now or trip_record).get("current_revision_number") or 0)

                    # Merge resolved airport codes into the constraint snapshot so the
                    # revision record carries canonical airport identity.
                    _constraint_update: dict = {}
                    if dest_iata:
                        _constraint_update["destination_airport"] = dest_iata
                        _constraint_update["destination_code"] = dest_iata
                    if orig_iata:
                        _constraint_update["origin_airport"] = orig_iata
                        _constraint_update["origin_code"] = orig_iata

                    _rev_result = ts.create_revision_atomic(
                        trip_id=target_trip_id,
                        user_id=current_user.id,
                        feedback=(
                            "First plan generated by AI planner." if _cur_rev == 0
                            else "Plan regenerated by AI planner."
                        ),
                        updated_constraints=_constraint_update,
                        # No expected_base_revision constraint — we accept the current
                        # authoritative revision and advance from it.  The row lock
                        # inside create_revision_atomic prevents races.
                    )

                    _new_rev_id = _rev_result.get("revision_id")
                    _new_rev_num = _rev_result.get("revision_number")
                    logger.info(
                        "Wave1B: trip %s PROPOSED revision created id=%s number=%s",
                        target_trip_id, _new_rev_id, _new_rev_num,
                    )

                    # Surface revision identity in the API response so the frontend
                    # can use the real revision_id immediately without a follow-up GET.
                    out_dict["revision_id"] = _new_rev_id
                    out_dict["revision_number"] = _new_rev_num
                    out_dict["revision_status"] = "PROPOSED"
                    out_dict["current_revision_id"] = _new_rev_id
                    out_dict["current_revision_number"] = _new_rev_num
                    if dest_iata:
                        out_dict["destination_airport"] = dest_iata
                    if orig_iata:
                        out_dict["origin_airport"] = orig_iata

                except Exception as _rev_err:
                    logger.error(
                        "Wave1B: revision creation failed for trip %s: %s",
                        target_trip_id, _rev_err, exc_info=True,
                    )
                    raise RuntimeError(
                        f"Plan generated and stored but revision creation failed for trip "
                        f"{target_trip_id}: {_rev_err}"
                    ) from _rev_err

        out_dict["success"] = True
        return JSONResponse(
            content=jsonable_encoder(out_dict)
        )

    except PermissionError as p_exc:
        raise HTTPException(status_code=403, detail=str(p_exc))
    except ValueError as v_exc:
        logger.error("Validation error in travel planner: %s", v_exc)
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": str(v_exc),
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        err_str = str(exc)
        err_code = getattr(exc, "status_code", getattr(exc, "code", None))
        if err_code == 503 or "503" in err_str or "Service Unavailable" in err_str or "overloaded" in err_str.lower():
            logger.warning("Upstream AI provider 503 in travel_planner: %s", exc)
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "error": "Upstream AI travel planning service is temporarily overloaded. Please try again shortly.",
                    "retryable": True,
                    "provider": "gemini",
                },
                headers={"Retry-After": "5"},
            )
        if err_code == 429 or "429" in err_str or "ResourceExhausted" in err_str or "rate limit" in err_str.lower():
            logger.warning("Upstream AI provider 429 in travel_planner: %s", exc)
            return JSONResponse(
                status_code=429,
                content={
                    "success": False,
                    "error": "AI planning rate limit exceeded. Please wait before retrying.",
                    "retryable": False,
                    "provider": "gemini",
                },
            )
        logger.exception("Error processing travel request: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "Travel planner processing failed.",
            },
        )


@app.post("/api/travel/pdf")
async def download_travel_plan_pdf(
    request_data: TravelPlanPDFRequest,
    current_user: UserResponse | None = Depends(get_optional_current_user),
):
    try:
        plan = None
        if request_data.plan:
            plan = request_data.plan
        elif request_data.trip_id:
            if not current_user:
                raise HTTPException(
                    status_code=401,
                    detail="Authentication required to export travel plan by trip ID.",
                )
            from services.trip_store import TripStore
            from services.evidence_store import EvidenceRevisionStore
            from services.booking_read_service import BookingReadService

            ts = TripStore()
            trip = ts.get_trip(request_data.trip_id, current_user.id)
            if not trip:
                raise HTTPException(
                    status_code=404,
                    detail="Trip not found or does not belong to you.",
                )

            ev_store = EvidenceRevisionStore()
            current_rev_id = trip.get("current_revision_id")
            rev = None
            if current_rev_id:
                rev = ev_store.get_revision(str(current_rev_id), current_user.id)

            meta = trip.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            if not isinstance(meta, dict):
                meta = {}

            ps = rev.plan_snapshot if (rev and isinstance(rev.plan_snapshot, dict)) else {}
            ps_meta = ps.get("metadata") if isinstance(ps.get("metadata"), dict) else {}
            ps_constraints = ps.get("constraints") if isinstance(ps.get("constraints"), dict) else {}
            t_constraints = meta.get("constraints") if isinstance(meta.get("constraints"), dict) else {}

            candidate_plan = (
                ps_meta.get("plan")
                or ps_meta.get("plan_json")
                or ps.get("plan")
                or ps.get("plan_json")
                or meta.get("plan")
                or meta.get("plan_json")
                or meta.get("generated_plan")
                or (ps if ps.get("daily_plan") or ps.get("route") else None)
                or {}
            )
            if isinstance(candidate_plan, str):
                try:
                    candidate_plan = json.loads(candidate_plan)
                except Exception:
                    candidate_plan = {}
            if not isinstance(candidate_plan, dict):
                candidate_plan = {}

            plan = dict(candidate_plan)

            # Ensure authoritative destination, origin, route, dates, duration, style, and itinerary are populated
            dest = plan.get("destination") or trip.get("destination") or ps.get("destination") or t_constraints.get("destination") or ""
            orig = plan.get("origin") or trip.get("origin") or ps.get("origin") or t_constraints.get("origin") or ""
            plan["destination"] = dest
            plan["origin"] = orig
            if not plan.get("route"):
                if orig and dest:
                    plan["route"] = f"{orig} to {dest}"
                elif trip.get("title"):
                    plan["route"] = trip.get("title")

            start_d = (
                plan.get("departure_date")
                or plan.get("start_date")
                or t_constraints.get("start_date")
                or t_constraints.get("departure_date")
                or ps_constraints.get("start_date")
                or ps_constraints.get("departure_date")
                or meta.get("start_date")
                or trip.get("start_date")
            )
            end_d = (
                plan.get("return_date")
                or plan.get("end_date")
                or t_constraints.get("end_date")
                or t_constraints.get("return_date")
                or ps_constraints.get("end_date")
                or ps_constraints.get("return_date")
                or meta.get("end_date")
                or trip.get("end_date")
            )
            if start_d:
                plan["departure_date"] = str(start_d)[:10]
            if end_d:
                plan["return_date"] = str(end_d)[:10]
            if not plan.get("travel_dates") or plan.get("travel_dates") == "Flexible dates":
                if start_d and end_d:
                    plan["travel_dates"] = f"{str(start_d)[:10]} to {str(end_d)[:10]}"

            if not plan.get("duration_days") or plan.get("duration_days") <= 0:
                if start_d and end_d:
                    try:
                        from datetime import date as _dt_date
                        d1 = _dt_date.fromisoformat(str(start_d)[:10])
                        d2 = _dt_date.fromisoformat(str(end_d)[:10])
                        plan["duration_days"] = max(1, (d2 - d1).days)
                    except Exception:
                        pass
                if not plan.get("duration_days"):
                    itin_len = len(plan.get("daily_plan") or plan.get("itinerary") or meta.get("itinerary") or [])
                    plan["duration_days"] = max(1, itin_len)

            if not plan.get("daily_plan"):
                itin = plan.get("itinerary") or meta.get("itinerary") or []
                if isinstance(itin, list) and itin:
                    plan["daily_plan"] = itin

            if not plan.get("trip_summary"):
                plan["trip_summary"] = meta.get("trip_summary") or meta.get("raw_plan") or (f"Custom itinerary for {dest}." if dest else "")

            # Query authoritative bookings for confirmed flight and hotel
            try:
                booking_read = BookingReadService()
                trip_bookings = booking_read.get_trip_bookings(request_data.trip_id, current_user.id)
                flight_info = trip_bookings.get("flight")
                hotel_info = trip_bookings.get("hotel")

                if flight_info and flight_info.get("is_booked"):
                    carrier = ""
                    slices = flight_info.get("slices") or []
                    if slices and isinstance(slices, list) and len(slices) > 0:
                        first_sl = slices[0]
                        carrier = first_sl.get("carrier_name") or first_sl.get("carrier_code") or ""
                        segs = first_sl.get("segments") or []
                        if segs and isinstance(segs, list) and len(segs) > 0:
                            f_num = segs[0].get("flight_number") or ""
                            if f_num:
                                carrier = f"{carrier} {f_num}".strip()
                    pnr = flight_info.get("pnr") or ""
                    ref = flight_info.get("booking_reference") or ""
                    pnr_ref = f" (PNR: {pnr}, Ref: {ref})" if pnr or ref else ""
                    route_info = f" ({orig}–{dest})" if orig and dest else ""
                    plan["confirmed_flight"] = f"Confirmed Booking: {carrier or 'Flight'}{route_info}{pnr_ref}"
                    if flight_info.get("amount"):
                        plan["confirmed_flight_cost"] = flight_info["amount"]

                if hotel_info and hotel_info.get("is_booked"):
                    h_name = hotel_info.get("hotel_name") or "Hotel"
                    r_name = hotel_info.get("room_name") or "Room"
                    cin = hotel_info.get("check_in") or ""
                    cout = hotel_info.get("check_out") or ""
                    code = hotel_info.get("confirmation_code") or ""
                    dates_str = f" ({cin} to {cout})" if cin and cout else ""
                    code_str = f" [Confirmation: {code}]" if code else ""
                    plan["confirmed_hotel"] = f"Confirmed Booking: {h_name} - {r_name}{dates_str}{code_str}"
                    if hotel_info.get("amount_inr"):
                        plan["confirmed_hotel_cost"] = hotel_info["amount_inr"]
            except Exception as b_err:
                logger.warning("Could not attach confirmed booking details to plan PDF for trip %s: %s", request_data.trip_id, b_err)
        else:
            raise HTTPException(
                status_code=400,
                detail="Either 'plan' or 'trip_id' must be provided.",
            )

        pdf = build_travel_plan_pdf(plan)
        filename = f"trust-trip-plan-{request_data.trip_id or 'export'}.pdf"
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except HTTPException:
        raise
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
@app.get("/api/health")
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
