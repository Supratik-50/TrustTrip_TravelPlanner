let currentThreadId = localStorage.getItem("travel_thread_id") || null;
let latestAnswerMarkdown = "";
let latestPlanJson = null;
let waitingForApproval = false;
let selectedHotel = null;
let hotelOffers = [];
let selectedHotelPrebook = null;

const AGENT_LABELS = {
  flight_agent: "✈️ Flight Agent",
  hotel_agent: "🏨 Hotel Agent",
  weather_agent: "🌦️ Weather Agent",
  budget_agent: "💰 Budget Agent",
  itinerary_agent: "🗓️ Itinerary Agent"
};

function setPrompt(text) {
  // Quick prompts always start a fresh planning request. Otherwise an old
  // persisted LangGraph thread can leak a previous route (e.g. Mumbai -> London)
  // into a new Japan/Dubai/Thailand request.
  currentThreadId = null;
  localStorage.removeItem("travel_thread_id");
  waitingForApproval = false;
  const approval = document.getElementById("approvalSection");
  if (approval) approval.classList.add("hidden");
  document.getElementById("userInput").value = text;
}

function setLoading(isLoading, mode = "draft") {
  const sendBtn = document.getElementById("sendBtn");
  const btnText = document.getElementById("btnText");
  const btnLoader = document.getElementById("btnLoader");
  const approveBtn = document.getElementById("approveBtn");
  const reviseBtn = document.getElementById("reviseBtn");

  sendBtn.disabled = isLoading;
  approveBtn.disabled = isLoading;
  reviseBtn.disabled = isLoading;

  if (isLoading && mode === "draft") {
    btnText.classList.add("hidden");
    btnLoader.classList.remove("hidden");
  } else {
    btnText.classList.remove("hidden");
    btnLoader.classList.add("hidden");
  }
}

function showError(message) {
  const errorBox = document.getElementById("errorBox");
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
  errorBox.scrollIntoView({ behavior: "smooth", block: "center" });
}

function hideError() {
  const errorBox = document.getElementById("errorBox");
  errorBox.classList.add("hidden");
  errorBox.textContent = "";
}

function renderMarkdown(element, markdown) {
  if (typeof marked !== "undefined") {
    element.innerHTML = marked.parse(markdown || "");
  } else {
    element.innerText = markdown || "";
  }
}

function formatInlineMarkdown(value) {
  const safe = esc(value || "");
  return safe
    .replace(/\*\*(.+?)\*\*/g, '<strong class="highlight-fact">$1</strong>')
    .replace(/__(.+?)__/g, '<strong class="highlight-fact">$1</strong>');
}

function showWorkflow(data) {
  const section = document.getElementById("workflowSection");
  const reasoning = document.getElementById("supervisorReasoning");
  const chips = document.getElementById("agentChips");
  const guardrailBadge = document.getElementById("guardrailBadge");

  reasoning.textContent = data.supervisor_reasoning || "Supervisor routing completed.";
  chips.innerHTML = "";

  (data.selected_agents || []).forEach((agent) => {
    const chip = document.createElement("span");
    chip.className = "agent-chip";
    chip.textContent = AGENT_LABELS[agent] || agent;
    chips.appendChild(chip);
  });

  if (data.guardrail_allowed === false) {
    guardrailBadge.textContent = "Guardrail blocked";
    guardrailBadge.classList.add("blocked");
  } else {
    guardrailBadge.textContent = "Guardrail passed";
    guardrailBadge.classList.remove("blocked");
  }

  section.classList.remove("hidden");
}

function passengerCountFromOffer(offer) {
  const list = Array.isArray(offer.passengers) ? offer.passengers : [];
  return list.length || Number(offer.passenger_count || 0) || 1;
}

function flightBadge(offer, index) {
  if (index === 0) return "⭐ BEST MATCH";
  if (offer.stops === 0) return "✈️ NON-STOP";
  if (index === 1) return "💰 VALUE OPTION";
  return `OPTION ${index + 1}`;
}

function renderAttractiveFlightCard(offer, index, target = "chat") {
  const first = offer.segments?.[0] || {};
  const last = offer.segments?.[offer.segments.length - 1] || first;
  const airline = offer.owner || first.marketing_carrier || first.operating_carrier || "Airline";
  const passengerCount = passengerCountFromOffer(offer);
  const total = Number(offer.price || 0);
  const perTraveller = passengerCount > 0 ? total / passengerCount : total;
  const currency = offer.currency || "";
  const inrTotal = offer.price_inr != null ? Number(offer.price_inr) : null;
  const inrPerTraveller = offer.price_per_traveller_inr != null ? Number(offer.price_per_traveller_inr) : (inrTotal != null ? inrTotal / passengerCount : null);
  const stopText = offer.stops === 0 ? "Non-stop" : `${offer.stops} stop${offer.stops > 1 ? "s" : ""}`;
  const segmentDuration = first.duration || "Duration unavailable";
  const bookable = offer.bookable !== false && offer.price != null;
  const modeLabel = offer.provider === "duffel" ? (offer.raw?.live_mode ? "LIVE PRICE" : "TEST PRICE") : "FLIGHT DATA";
  const primaryPerTraveller = inrPerTraveller != null ? `₹${inrPerTraveller.toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2})}` : `${esc(currency)} ${perTraveller.toFixed(2)}`;
  const primaryTotal = inrTotal != null ? `₹${inrTotal.toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2})} total` : `${esc(currency)} ${total.toFixed(2)} total`;
  const originalPrice = currency && currency !== "INR" ? `<small class="flight-original-price">Duffel fare: ${esc(currency)} ${perTraveller.toFixed(2)} / traveller · ${esc(currency)} ${total.toFixed(2)} total</small>` : "";
  return `<article class="premium-flight-card ${index === 0 ? "featured" : ""}">
    <div class="premium-flight-top">
      <span class="flight-badge">${flightBadge(offer, index)}</span>
      <span class="flight-provider">Duffel · ${esc(modeLabel)}</span>
    </div>
    <div class="premium-flight-airline">
      <div class="airline-logo-placeholder">✈️</div>
      <div><strong>${esc(airline)}</strong><small>${esc(first.flight_number || "Duffel flight offer")}</small></div>
    </div>
    <div class="premium-route">
      <div class="route-point"><strong>${esc(first.origin || "—")}</strong><span>${flightTime(first.departure)}</span></div>
      <div class="route-line"><span>${esc(segmentDuration)}</span><i></i><small>${esc(stopText)}</small></div>
      <div class="route-point arrival"><strong>${esc(last.destination || "—")}</strong><span>${flightTime(last.arrival)}</span></div>
    </div>
    <div class="premium-flight-meta">
      <span>👥 ${passengerCount} traveller${passengerCount === 1 ? "" : "s"}</span>
      <span>🧳 Fare details available</span>
      <span>${offer.expires_at ? `⏱ Expires ${esc(flightTime(offer.expires_at))}` : "Duffel fare"}</span>
    </div>
    <div class="flight-inventory-note">
      ${Number.isInteger(offer.available_seats) ? `🪑 ${offer.available_seats} seats reported available` : `ℹ️ Exact remaining seat count is not supplied by Duffel; availability will be rechecked before booking.`}
    </div>
    <div class="premium-flight-price">
      <div><small>${bookable ? "Price / traveller" : "Price"}</small><strong>${bookable ? primaryPerTraveller : "Not supplied"}</strong><span>${bookable ? `${primaryTotal} for ${passengerCount} traveller${passengerCount === 1 ? "" : "s"}` : "Duffel did not return a bookable fare price."}</span>${originalPrice}</div>
      ${bookable ? `<button type="button" onclick="selectFlight(${index})">Select flight</button>` : `<button type="button" disabled>Not bookable</button>`}
    </div>
  </article>`;
}

function showChatFlightRecommendations(data) {
  const section = document.getElementById("chatFlightSection");
  const results = document.getElementById("chatFlightResults");
  const subtitle = document.getElementById("chatFlightSubtitle");
  if (!section || !results) return;

  const offers = data.live_flight_offers || [];
  flightOffers = offers;
  const c = data.trip_constraints || {};
  const status = data.flight_search_status || "ok";
  const provider = data.flight_provider || "duffel";

  if (!c.destination) {
    section.classList.add("hidden");
    return;
  }

  const headline = section.querySelector(".chat-flight-header h2");
  const recommendationDate = c.flight_recommendation_date || c.departure_date;
  if (headline) headline.textContent = `✈️ Flights matching your trip${recommendationDate ? ` · ${recommendationDate}` : ""}`;
  subtitle.textContent = `${c.origin_city || c.origin || "Departure city not specified"} → ${c.destination_city || c.destination || "Destination"} · ${Number(c.passenger_count || 1)} traveller${Number(c.passenger_count || 1) === 1 ? "" : "s"}`;

  if (!offers.length) {
    let message = "No flight offers were returned for this request.";
    if (!c.origin) message = `Your ${esc(c.destination_city || c.destination)} trip is ready. Add a departure city or IATA airport in the planner to get a live flight recommendation for 1 traveller.`;
    else if (status === "provider_error") message = "Live flight data could not be retrieved right now. I will not invent a flight or price.";
    else if (!c.departure_date && !c.flight_recommendation_date) message = "A live flight recommendation needs a concrete departure city and destination city/airport. Enter the route to search automatically.";
    else if (status === "no_offers") message = `No ${provider === "duffel" ? (data.duffel_mode === "test" ? "test" : "live") : "live"} flight offer was returned for this exact route/date. I will not invent a flight or price.`;
    results.innerHTML = `<div class="empty-state chat-flight-empty"><strong>${esc(message)}</strong><p>Try another date or route, or use Manual Flight Search to compare available options.</p><button type="button" class="manual-flight-btn" onclick="openManualFlightSearch()">🔍 Search manually</button></div>`;
    section.classList.remove("hidden");
    return;
  }

  const priced = offers.filter(o => o.price != null);
  if (!priced.length) {
    results.innerHTML = `<div class="empty-state chat-flight-empty"><strong>Flight information was found, but no bookable fare price was returned.</strong><p>Duffel did not return a priced bookable offer for this search.</p><button type="button" class="manual-flight-btn" onclick="openManualFlightSearch()">🔍 Open Flight Search</button></div>`;
    section.classList.remove("hidden");
    return;
  }

  results.innerHTML = offers.slice(0, 6).map((offer, index) => renderAttractiveFlightCard(offer, index)).join("");
  section.classList.remove("hidden");
}

function openManualFlightSearch() {
  const section = document.querySelector(".flight-booking-card");
  if (section) section.scrollIntoView({behavior:"smooth", block:"start"});
  const input = document.getElementById("flightOrigin");
  if (input) setTimeout(() => input.focus(), 450);
}

function showLivePlanning(data) {
  const section = document.getElementById("livePlanningSection");
  const grid = document.getElementById("livePlanningGrid");
  const badge = document.getElementById("planningModeBadge");
  if (!section || !grid) return;
  const offers = data.live_flight_offers || [];
  const budget = data.live_plan?.budget || {};
  badge.textContent = data.replanning_mode === "cheaper" ? "Cheaper mode · live re-search" : "Live provider refresh";
  flightOffers = offers;
  grid.innerHTML = `
    <div class="live-stat"><span>Route</span><strong>${esc(data.trip_constraints?.origin || "—")} → ${esc(data.trip_constraints?.destination || "—")}</strong><small>${esc(data.trip_constraints?.origin_city || "")} to ${esc(data.trip_constraints?.destination_city || "")}</small></div>
    <div class="live-stat"><span>Live flights</span><strong>${offers.length}</strong><small>${offers.length ? `${esc(offers[0].currency || "")} ${Number(offers[0].price || 0).toFixed(0)} cheapest shown` : "Add a route/date to search"}</small></div>
    <div class="live-stat"><span>Budget estimate</span><strong>${budget.currency ? `${esc(budget.currency)} ${Number(budget.estimated_total || 0).toFixed(0)}` : "—"}</strong><small>${budget.target_budget ? `Target ${esc(String(budget.target_budget))}` : "Planning estimate"}</small></div>
    <div class="live-stat"><span>Replanning</span><strong>${data.replanning_mode === "cheaper" ? "Cost-first" : "Value-first"}</strong><small>Providers queried again for this request</small></div>
    ${offers.length ? `<div class="live-flight-options"><h3>Live flight options</h3>${offers.slice(0,5).map((_, i) => renderFlightCard(_, i)).join("")}</div>` : ""}`;
  section.classList.remove("hidden");
}

function renderStructuredPlan(plan, fallbackMarkdown) {
  if (!plan || !plan.route) {
    return typeof marked !== "undefined" ? marked.parse(fallbackMarkdown || "") : esc(fallbackMarkdown || "");
  }

  const days = (plan.daily_plan || []).map(day => `
    <article class="plan-day-card">
      <div class="plan-day-head"><span>DAY ${esc(day.day)}</span><strong>${esc(day.title)}</strong></div>
      <div class="plan-period"><b>Morning</b><span>${esc(day.morning)}</span></div>
      <div class="plan-period"><b>Afternoon</b><span>${esc(day.afternoon)}</span></div>
      <div class="plan-period"><b>Evening</b><span>${esc(day.evening)}</span></div>
      <div class="plan-day-cost">Estimated day cost: ₹${Number(day.estimated_daily_cost_inr || 0).toLocaleString('en-IN')}</div>
    </article>`).join("");

  const b = plan.budget || {};
  const changes = (plan.changes_from_feedback || []).map(x => `<li>${esc(x)}</li>`).join("");
  const warnings = (plan.warnings || []).map(x => `<li>${esc(x)}</li>`).join("");

  return `
    <div class="structured-plan">
      <div class="plan-hero">
        <span class="eyebrow">${esc(plan.travel_style || 'Trip Plan')}</span>
        <h1>${esc(plan.route || 'Travel Plan')}</h1>
        <p>${esc(plan.trip_summary || '')}</p>
        <div class="plan-meta"><span>${esc(plan.travel_dates || 'Flexible dates')}</span><span>${esc(plan.duration_days)} days</span></div>
      </div>
      <h2>Day-by-Day Itinerary</h2>
      <div class="plan-days">${days}</div>
      <div class="plan-budget">
        <h2>Budget</h2>
        <div class="budget-grid">
          <div><span>Flights</span><strong>₹${Number(b.flight_estimate_inr || 0).toLocaleString('en-IN')}</strong></div>
          <div><span>Hotels</span><strong>₹${Number(b.hotel_estimate_inr || 0).toLocaleString('en-IN')}</strong></div>
          <div><span>Transport</span><strong>₹${Number(b.local_transport_estimate_inr || 0).toLocaleString('en-IN')}</strong></div>
          <div><span>Activities</span><strong>₹${Number(b.activities_estimate_inr || 0).toLocaleString('en-IN')}</strong></div>
          <div><span>Contingency</span><strong>₹${Number(b.contingency_inr || 0).toLocaleString('en-IN')}</strong></div>
          <div class="budget-total"><span>Total</span><strong>₹${Number(b.estimated_total_inr || 0).toLocaleString('en-IN')}</strong></div>
        </div>
        <p class="budget-note">${esc(b.pricing_note || '')}</p>
        <p class="budget-status ${b.within_budget ? 'ok' : 'warning'}">${b.within_budget ? 'Within target budget' : 'Above target budget'}</p>
      </div>
      <div class="plan-info-grid">
        <div class="plan-info-card plan-info-flight"><h3>✈️ Flight suggestion</h3><p>${formatInlineMarkdown(plan.flight_suggestion)}</p></div>
        <div class="plan-info-card plan-info-hotel"><h3>🏨 Accommodation</h3><p>${formatInlineMarkdown(plan.accommodation_plan)}</p></div>
        <div class="plan-info-card plan-info-weather"><h3>🌦️ Weather guidance</h3><p>${formatInlineMarkdown(plan.weather_guidance)}</p></div>
      </div>
      ${changes ? `<div class="revision-box"><h3>Changes from your feedback</h3><ul>${changes}</ul></div>` : ''}
      ${warnings ? `<div class="warning-box"><h3>Important notes</h3><ul>${warnings}</ul></div>` : ''}
    </div>`;
}

function showResult(answer, threadId, isDraft = false, planJson = null, provider = "") {
  latestAnswerMarkdown = answer || "";
  latestPlanJson = planJson && Object.keys(planJson).length ? planJson : null;

  const resultSection = document.getElementById("resultSection");
  const resultBox = document.getElementById("resultBox");
  const threadInfo = document.getElementById("threadInfo");
  const resultTitle = document.getElementById("resultTitle");

  resultBox.innerHTML = renderStructuredPlan(planJson, latestAnswerMarkdown);
  threadInfo.textContent = `Thread ID: ${threadId}${provider ? ` · Planner: ${provider}` : ""}`;
  resultTitle.textContent = isDraft ? "Draft Travel Plan — Review Required" : "Your Final AI Travel Plan";
  resultSection.classList.remove("hidden");

  resultSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

function showApproval(data) {
  waitingForApproval = true;
  const section = document.getElementById("approvalSection");
  const approvalRequest = document.getElementById("approvalRequest");
  approvalRequest.textContent = data.approval_request ||
    "Approve the draft or provide feedback before the final plan is generated.";
  section.classList.remove("hidden");
}

function hideApproval() {
  waitingForApproval = false;
  document.getElementById("approvalSection").classList.add("hidden");
  document.getElementById("approvalFeedback").value = "";
}

async function sendMessage() {
  hideError();

  if (waitingForApproval) {
    showError("Please approve or revise the current draft before starting another plan.");
    return;
  }

  const input = document.getElementById("userInput");
  const message = input.value.trim();

  if (!message) {
    showError("Please enter your travel request first.");
    return;
  }

  setLoading(true, "draft");

  try {
    const response = await fetch("/api/travel", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        message: message,
        thread_id: currentThreadId
      })
    });

    const data = await response.json();

    if (!response.ok || !data.success) {
      throw new Error(data.error || "Something went wrong.");
    }

    currentThreadId = data.thread_id;
    localStorage.setItem("travel_thread_id", currentThreadId);

    showWorkflow(data);
    showLivePlanning(data);
    showChatFlightRecommendations(data);
    showChatHotelRecommendations(data);

    if (data.requires_approval) {
      showResult(data.itinerary || data.answer, data.thread_id, true, data.plan_json, data.llm_provider);
      showApproval(data);
    } else {
      hideApproval();
      showResult(data.answer, data.thread_id, false, data.plan_json, data.llm_provider);
    }
  } catch (error) {
    showError(error.message);
  } finally {
    setLoading(false, "draft");
  }
}

async function submitApproval(approved) {
  hideError();

  if (!currentThreadId || !waitingForApproval) {
    showError("There is no draft waiting for approval.");
    return;
  }

  const feedbackInput = document.getElementById("approvalFeedback");
  const feedback = feedbackInput.value.trim();

  if (!approved && !feedback) {
    showError("Please enter revision feedback before requesting changes.");
    feedbackInput.focus();
    return;
  }

  setLoading(true, "approval");

  try {
    const response = await fetch("/api/travel/approve", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        thread_id: currentThreadId,
        approved: approved,
        feedback: feedback
      })
    });

    const data = await response.json();

    if (!response.ok || !data.success) {
      throw new Error(data.error || "Could not resume the travel workflow.");
    }

    showWorkflow(data);
    showLivePlanning(data);
    showChatFlightRecommendations(data);
    showChatHotelRecommendations(data);
    hideApproval();
    showResult(data.answer, data.thread_id, false, data.plan_json, data.llm_provider);
  } catch (error) {
    showError(error.message);
  } finally {
    setLoading(false, "approval");
  }
}

function copyResult() {
  const resultBox = document.getElementById("resultBox");
  const text = resultBox.innerText;

  if (!text) {
    return;
  }

  navigator.clipboard.writeText(text)
    .then(() => {
      const copyBtn = document.querySelector(".copy-btn");
      const oldText = copyBtn.textContent;
      copyBtn.textContent = "Copied!";

      setTimeout(() => {
        copyBtn.textContent = oldText;
      }, 1400);
    })
    .catch(() => {
      showError("Could not copy result.");
    });
}

async function downloadPDF() {
  if (!latestPlanJson || !Object.keys(latestPlanJson).length) {
    showError("No structured travel plan is available to export. Generate the plan first.");
    return;
  }
  const downloadBtn = document.querySelector(".download-btn");
  const oldText = downloadBtn.textContent;
  downloadBtn.textContent = "Preparing PDF...";
  downloadBtn.disabled = true;
  try {
    const response = await fetch("/api/travel/pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan: latestPlanJson })
    });
    if (!response.ok) {
      let detail = "Could not generate the travel plan PDF.";
      try { const data = await response.json(); detail = data.error || detail; } catch (_) {}
      throw new Error(detail);
    }
    const blob = await response.blob();
    if (!blob.size || blob.type !== "application/pdf") throw new Error("The server returned an invalid PDF.");
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "trust-trip-travel-plan.pdf";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    console.error("Travel plan PDF export failed", error);
    showError(error.message || "Could not download the travel plan PDF.");
  } finally {
    downloadBtn.textContent = oldText;
    downloadBtn.disabled = false;
  }
}

document.addEventListener("keydown", function(event) {
  if (event.ctrlKey && event.key === "Enter") {
    sendMessage();
  }
});



function hotelBadge(offer,index){
  if(index===0) return "⭐ BEST MATCH";
  if(offer.refundable) return "🛡️ FLEXIBLE";
  return index===1 ? "💰 VALUE OPTION" : `OPTION ${index+1}`;
}
function renderHotelCard(offer,index){
  const price=offer.price_inr!=null ? `₹${Number(offer.price_inr).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2})}` : `${esc(offer.currency||'')} ${Number(offer.price||0).toFixed(2)}`;
  const original=offer.currency && String(offer.currency).toUpperCase()!=="INR" ? `<small class="hotel-original-price">Provider: ${esc(offer.currency)} ${Number(offer.price||0).toFixed(2)}</small>` : "";
  const rating=offer.rating!=null ? `⭐ ${Number(offer.rating).toFixed(1)}` : (offer.stars ? `⭐ ${esc(offer.stars)}` : "Rating not supplied");
  const cancellation=offer.refundable ? "Free cancellation / refundable rate" : "Cancellation terms apply";
  return `<article class="hotel-card ${index===0?'featured':''}">
    <div class="hotel-card-image">${offer.photo ? `<img src="${esc(offer.photo)}" alt="${esc(offer.hotel_name||'Hotel')}" loading="lazy" onerror="this.style.display='none';this.parentElement.innerHTML='🏨'">` : '🏨'}</div>
    <div class="hotel-card-top"><span class="flight-badge">${hotelBadge(offer,index)}</span><span class="hotel-provider">${esc(offer.provider||'Hotel provider')}</span></div>
    <h3>${esc(offer.hotel_name||'Hotel')}</h3><small>${esc(offer.address||'Location details supplied by provider')}</small>
    <div class="hotel-card-meta"><span>${rating}</span><span>🛏️ ${esc(offer.room_name||'Room')}</span><span>🍳 ${esc(offer.board_name||'Room only')}</span><span>🛡️ ${esc(cancellation)}</span></div>
    <div class="hotel-card-bottom"><div class="hotel-price"><small>Total stay</small><strong>${price}</strong>${original}</div><button type="button" onclick="selectHotel(${index})">Book Now</button></div>
  </article>`;
}
function showChatHotelRecommendations(data){
  const section=document.getElementById('chatHotelSection'); const results=document.getElementById('chatHotelResults'); const subtitle=document.getElementById('chatHotelSubtitle');
  if(!section||!results)return;
  hotelOffers=data.live_hotel_offers||[]; const c=data.trip_constraints||{};
  if(!c.destination && !hotelOffers.length){section.classList.add('hidden');return;}
  subtitle.textContent=`${c.destination_city||c.destination||'Destination'}${c.departure_date?` · check-in ${c.departure_date}`:''} · ranked across Hotelbeds + LiteAPI`;
  if(!hotelOffers.length){ results.innerHTML=`<div class="empty-state"><strong>No live hotel offers were returned.</strong><p>${esc(data.hotel_search_status==='missing_date'?'A check-in date is required for automatic priced hotel recommendations.':'Try another date/destination or use Manual Hotel Search.')}</p><button type="button" class="manual-flight-btn" onclick="openManualHotelSearch()">🔍 Search manually</button></div>`; section.classList.remove('hidden'); return; }
  results.innerHTML=hotelOffers.slice(0,6).map(renderHotelCard).join(''); section.classList.remove('hidden');
}
function openManualHotelSearch(){ const el=document.getElementById('hotelSearchCard'); if(el)el.scrollIntoView({behavior:'smooth',block:'start'}); const input=document.getElementById('hotelDestination'); if(input)setTimeout(()=>input.focus(),450); }
function showHotelError(message){const b=document.getElementById('hotelError');b.textContent=message;b.classList.remove('hidden');}
function hideHotelError(){const b=document.getElementById('hotelError');b.classList.add('hidden');b.textContent='';}
async function searchHotels(){
  hideHotelError(); const destination=document.getElementById('hotelDestination').value.trim(); const checkin=document.getElementById('hotelCheckin').value; const checkout=document.getElementById('hotelCheckout').value;
  const adults=Number(document.getElementById('hotelAdults').value||1), children=Number(document.getElementById('hotelChildren').value||0), rooms=Number(document.getElementById('hotelRooms').value||1), budget=Number(document.getElementById('hotelBudget').value||0); const hb=document.getElementById('hotelbedsDestinationCode').value.trim().toUpperCase();
  if(!destination)return showHotelError('Enter a destination.'); if(!checkin||!checkout)return showHotelError('Choose check-in and check-out dates.'); if(checkout<=checkin)return showHotelError('Check-out must be after check-in.');
  const btn=document.getElementById('hotelSearchBtn');btn.disabled=true;btn.textContent='Searching both providers...';
  try{const r=await fetch('/api/hotels/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({destination,checkin,checkout,adults,children,rooms,budget_inr:budget||null,cheaper_mode:false,hotelbeds_destination_code:hb||null})});const d=await r.json();if(!r.ok||!d.success)throw new Error(d.detail||d.error||'Hotel search failed.');hotelOffers=d.offers||[];const out=document.getElementById('hotelResults');out.innerHTML=hotelOffers.length?`<div class="flight-results-header"><div><h3>${d.count} hotel offer(s)</h3><p>Normalized and ranked using price, budget fit, rating, cancellation and board information.</p></div><span class="provider-badge">Hotelbeds + LiteAPI</span></div>${hotelOffers.map(renderHotelCard).join('')}`:`<div class="empty-state">No hotel offers were returned. No price or availability will be invented.</div>`;out.classList.remove('hidden');out.scrollIntoView({behavior:'smooth',block:'start'});}catch(e){showHotelError(e.message)}finally{btn.disabled=false;btn.textContent='Search Hotels';}
}
function resetHotelBookingUI(){
  const section=document.getElementById('hotelBookingSection');
  const summary=document.getElementById('hotelBookingSummary');
  const guestForm=document.getElementById('hotelGuestForm');
  const actions=document.querySelector('#hotelBookingSection .approval-actions');
  const pay=document.getElementById('hotelPayBtn');
  if(section) section.classList.remove('hotel-booking-complete');
  if(guestForm) guestForm.classList.remove('hidden');
  if(actions) actions.classList.remove('hidden');
  if(pay){ pay.disabled=true; pay.textContent='Checking final hotel price...'; }
  if(summary) summary.textContent='Review the final hotel quote before payment.';
}

async function selectHotel(index){
  selectedHotel=hotelOffers[index]; if(!selectedHotel||selectedHotel.bookable===false)return showHotelError('This hotel offer is not bookable.'); hideHotelError();
  // A previous successful booking must never leave the next hotel selection in the completed/hidden state.
  // Reset the booking panel before every new hotel selection so Razorpay can be opened again.
  selectedHotelPrebook=null;
  resetHotelBookingUI();
  const section=document.getElementById('hotelBookingSection'); const summary=document.getElementById('hotelBookingSummary'); const pay=document.getElementById('hotelPayBtn'); pay.disabled=true; pay.textContent='Checking final hotel price...'; section.classList.remove('hidden');
  try{const r=await fetch('/api/hotels/prebook',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({offer:selectedHotel})});const d=await r.json();if(!r.ok||!d.success)throw new Error(d.detail||'Hotel offer is no longer available.');selectedHotelPrebook=d.prebook; const rooms=Math.max(1,Number(selectedHotel.rooms||selectedHotelPrebook.rooms||1)); summary.innerHTML=`<strong>${esc(selectedHotel.hotel_name)}</strong> · ${esc(selectedHotel.room_name||'Room')} · ${esc(selectedHotel.board_name||'Room only')}<br><strong>Rooms: ${rooms}</strong> · <strong>Reference price: ₹${Number(selectedHotel.price_inr||0).toLocaleString('en-IN',{minimumFractionDigits:2})}</strong> · Provider ${esc(selectedHotel.currency||'')} ${Number(selectedHotel.price||0).toFixed(2)}<br><small>Final availability was checked by the hotel provider. Enter one lead guest's details; the same booking identity will be used for all selected rooms.</small>`;document.getElementById('hotelGuestForm').innerHTML=`<div class="passenger-fields"><input id="hotelFirstName" placeholder="Lead guest first name" required><input id="hotelLastName" placeholder="Lead guest last name" required><input id="hotelEmail" type="email" placeholder="Email" required><input id="hotelPhone" placeholder="Phone (+91...)" required></div><small class="hotel-lead-guest-note">One lead guest is enough for ${rooms} room${rooms===1?'':'s'}; separate guest forms are not required.</small>`;pay.disabled=false;pay.textContent='Review & Pay with Razorpay';section.scrollIntoView({behavior:'smooth',block:'center'});}catch(e){summary.innerHTML=`<strong>Hotel recheck failed.</strong><br><small>${esc(e.message)}</small>`;pay.disabled=true;pay.textContent='Offer unavailable';}
}
async function confirmHotelBooking(){
  if(!selectedHotel||!selectedHotelPrebook?.prebook_id)return showHotelError('Select a hotel and wait for the final price check.');
  const first=document.getElementById('hotelFirstName')?.value.trim(), last=document.getElementById('hotelLastName')?.value.trim(), email=document.getElementById('hotelEmail')?.value.trim(), phone=document.getElementById('hotelPhone')?.value.trim();
  if(!first||!last||!email||!phone)return showHotelError('First name, last name, email and phone are required.');
  const btn=document.getElementById('hotelPayBtn');btn.disabled=true;btn.textContent='Creating secure payment...';hideHotelError();
  try{const holder={firstName:first,lastName:last,email,phone};const rooms=Math.max(1,Number(selectedHotel.rooms||selectedHotelPrebook.rooms||1));const guests=Array.from({length:rooms},(_,i)=>({occupancyNumber:i+1,firstName:first,lastName:last,email,phone}));const orderResp=await fetch('/api/hotel-payments/create-order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({offer:selectedHotel,prebook:selectedHotelPrebook,holder,guests,customer_ref:getCustomerRef(),final_confirmation:true})});const od=await orderResp.json();if(!orderResp.ok||!od.success)throw new Error(paymentErrorMessage(od,'Could not create hotel payment order.'));if(typeof Razorpay==='undefined')throw new Error('Razorpay Checkout could not be loaded.');
    const options={key:od.key_id,amount:Math.round(Number(od.amount_inr)*100),currency:'INR',name:'Trust Trip MAS',description:`Hotel booking — ${selectedHotel.hotel_name}`,order_id:od.razorpay_order_id,notes:{transaction_id:od.transaction_id,hotel_offer_id:selectedHotel.id},handler:async function(resp){try{btn.textContent='Verifying payment and booking hotel...';const vr=await fetch('/api/hotel-payments/verify-and-book',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({transaction_id:od.transaction_id,razorpay_order_id:resp.razorpay_order_id,razorpay_payment_id:resp.razorpay_payment_id,razorpay_signature:resp.razorpay_signature,offer:selectedHotel,prebook:selectedHotelPrebook,holder,guests,customer_ref:getCustomerRef(),final_confirmation:true})});const vd=await vr.json();if(!vr.ok||!vd.success){if(vd.requires_refund)throw new Error('Payment succeeded but hotel booking failed. Do not pay again; contact support for refund/reconciliation.');throw new Error(paymentErrorMessage(vd,'Hotel payment/booking failed.'));}const b=vd.booking||{};document.getElementById('hotelBookingSummary').innerHTML=`<div class="hotel-success-state"><strong>✓ Hotel booking completed.</strong><br><span>Confirmation: <strong>${esc(b.confirmation_code||b.booking_id||'Confirmed')}</strong></span><br><span>Provider: ${esc(b.provider||selectedHotel.provider)}</span><br><span>Rooms: ${rooms}</span><br><span>Lead guest: ${esc(first+' '+last)}</span><br><strong>Paid ₹${Number(vd.amount_inr||od.amount_inr).toLocaleString('en-IN',{minimumFractionDigits:2})} through Razorpay TEST.</strong><br><small>Your hotel booking has been saved. The confirmation PDF is now available in Booked Hotels.</small></div>`;document.getElementById('hotelGuestForm').classList.add('hidden');document.querySelector('#hotelBookingSection .approval-actions').classList.add('hidden');document.getElementById('hotelBookingSection').classList.add('hotel-booking-complete');selectedHotel=null;selectedHotelPrebook=null;await loadBookedHotels();const hotelsDrop=document.getElementById('bookedHotelsDropdown');if(hotelsDrop?.classList.contains('hidden'))toggleBookedHotels();}catch(e){showHotelError(e.message);btn.disabled=false;btn.textContent='Review & Pay with Razorpay';}},modal:{ondismiss:()=>{btn.disabled=false;btn.textContent='Review & Pay with Razorpay';}}};const checkout=new Razorpay(options);checkout.on('payment.failed',f=>{showHotelError(f.error?.description||'Razorpay payment failed.');btn.disabled=false;btn.textContent='Review & Pay with Razorpay';});checkout.open();
  }catch(e){showHotelError(e.message);btn.disabled=false;btn.textContent='Review & Pay with Razorpay';}
}
function cancelHotelBooking(){selectedHotel=null;selectedHotelPrebook=null;const section=document.getElementById('hotelBookingSection');section.classList.add('hidden');resetHotelBookingUI();}

// =========================
// Flight Booking Agent UI
// =========================
let selectedFlight = null;
let flightOffers = [];

function setDefaultFlightDate() {
  const input = document.getElementById("flightDeparture");
  if (!input || input.value) return;
  const d = new Date();
  d.setDate(d.getDate() + 14);
  input.value = d.toISOString().slice(0, 10);
}
setDefaultFlightDate();
function setDefaultHotelDates(){
  const a=document.getElementById('hotelCheckin'), b=document.getElementById('hotelCheckout');
  if(!a||!b)return; const d=new Date(); d.setDate(d.getDate()+14); const e=new Date(d); e.setDate(e.getDate()+4);
  if(!a.value)a.value=d.toISOString().slice(0,10); if(!b.value)b.value=e.toISOString().slice(0,10);
}
setDefaultHotelDates();

function showFlightError(message) {
  const box = document.getElementById("flightError");
  box.textContent = message;
  box.classList.remove("hidden");
}
function hideFlightError() {
  const box = document.getElementById("flightError");
  box.classList.add("hidden");
  box.textContent = "";
}
function esc(value) {
  return String(value ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
}
function flightTime(value) {
  if (!value) return "—";
  try { return new Date(value).toLocaleString([], {dateStyle:"short", timeStyle:"short"}); }
  catch (_) { return value; }
}
function renderFlightCard(offer, index) {
  const first = offer.segments?.[0] || {};
  const last = offer.segments?.[offer.segments.length - 1] || first;
  const airline = offer.owner || first.operating_carrier || "Airline";
  const stopText = offer.stops === 0 ? "Direct" : `${offer.stops} stop${offer.stops > 1 ? "s" : ""}`;
  const price = offer.price == null ? "Price unavailable" : `${offer.currency || ""} ${Number(offer.price).toFixed(2)}`;
  return `<article class="flight-card">
    <div class="flight-card-top"><span class="flight-rank">${index === 0 ? "AI BEST VALUE" : `OPTION ${index + 1}`}</span><strong>${esc(airline)}</strong></div>
    <div class="flight-route"><div><b>${esc(first.origin || "")}</b><span>${flightTime(first.departure)}</span></div><div class="flight-arrow">→</div><div><b>${esc(last.destination || "")}</b><span>${flightTime(last.arrival)}</span></div></div>
    <div class="flight-meta"><span>${esc(stopText)}</span><span>${esc(first.duration || "Duration unavailable")}</span><span>${esc(offer.expires_at ? "Offer expires " + flightTime(offer.expires_at) : "Fare availability may change")}</span></div>
    <div class="flight-card-bottom"><strong>${esc(price)}</strong><button onclick="selectFlight(${index})">Select</button></div>
  </article>`;
}
async function searchFlights() {
  hideFlightError();
  const origin = document.getElementById("flightOrigin").value.trim().toUpperCase();
  const destination = document.getElementById("flightDestination").value.trim().toUpperCase();
  const departure = document.getElementById("flightDeparture").value;
  const returnDate = document.getElementById("flightReturn").value;
  const adults = Number(document.getElementById("flightAdults").value || 1);
  const children = Number(document.getElementById("flightChildren")?.value || 0);
  const infants = Number(document.getElementById("flightInfants")?.value || 0);
  const cabin = document.getElementById("flightCabin").value;
  const maxStops = Number(document.getElementById("flightStops").value);
  if (!/^[A-Z]{3}$/.test(origin) || !/^[A-Z]{3}$/.test(destination)) return showFlightError("Enter valid 3-letter IATA airport codes, for example CCU and DEL.");
  if (!departure) return showFlightError("Please choose a departure date.");
  if (returnDate && returnDate <= departure) return showFlightError("Return date must be after departure date.");

  const btn = document.getElementById("flightSearchBtn");
  btn.disabled = true; btn.textContent = "Searching...";
  try {
    const slices = [{origin, destination, departure_date: departure}];
    if (returnDate) slices.push({origin: destination, destination: origin, departure_date: returnDate});
    const response = await fetch("/api/flights/search", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({slices, adults, children, infants, cabin_class:cabin, max_connections:maxStops})});
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error || data.detail || "Flight search failed.");
    const results = document.getElementById("flightResults");
    flightOffers = data.offers || [];
    results.innerHTML = flightOffers.length ? `<div class="flight-results-header"><div><h3>${data.count} flight offer(s)</h3><p>${"Ranked by transparent fare/stops scoring. The displayed fare comes from Duffel; INR is shown alongside the original provider currency."}</p></div><span class="provider-badge">${esc(data.provider)}${data.test_mode ? " · TEST" : " · LIVE"}</span></div>${flightOffers.slice(0, 20).map((offer, index) => renderAttractiveFlightCard(offer, index, "manual")).join("")}` : `<div class="empty-state">No flight offers were returned. No price or flight will be invented. Try another date/route or switch to the configured provider.</div>`;
    results.classList.remove("hidden");
    results.scrollIntoView({behavior:"smooth", block:"start"});
  } catch (error) { showFlightError(error.message); }
  finally { btn.disabled = false; btn.textContent = "Search Flights"; }
}
function getCustomerRef() {
  let ref = localStorage.getItem("trust_trip_customer_ref");
  if (!ref) {
    ref = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : `customer_${Date.now()}_${Math.random().toString(36).slice(2)}`;
    localStorage.setItem("trust_trip_customer_ref", ref);
  }
  return ref;
}

function presentationDemoBooking(pricing, orderData, providerAmount, providerCurrency, customerAmount, customerCurrency) {
  const demoPnr = `TT-DEMO-${Math.random().toString(36).slice(2, 8).toUpperCase()}`;
  const demoCard = "•••• •••• •••• 4242";
  document.getElementById("flightBookingSummary").innerHTML = `
    <div class="demo-success-banner">
      <strong>✓ DEMO FLIGHT BOOKING SUCCESSFUL</strong>
      <span>Presentation Mode — no real flight was issued and no real card number is displayed.</span>
    </div>
    <div class="demo-ticket">
      <div><span>PNR / Booking Reference</span><strong>${esc(demoPnr)}</strong></div>
      <div><span>Payment</span><strong>Razorpay TEST MODE</strong></div>
      <div><span>Demo Card</span><strong>${demoCard}</strong></div>
      <div><span>Amount</span><strong>${esc(customerCurrency)} ${Number(customerAmount).toFixed(2)}</strong></div>
      <div><span>Supplier Fare</span><strong>${esc(providerCurrency)} ${Number(providerAmount).toFixed(2)}</strong></div>
    </div>
    <small>Demo PNR and card display are synthetic presentation data. Transaction: ${esc(orderData.transaction_id)}.</small>`;
}

function paymentErrorMessage(data, fallback) {
  if (!data) return fallback;
  if (typeof data.detail === "string") return data.detail;
  if (typeof data.error === "string") return data.error;
  return fallback;
}

function selectFlight(index) {
  selectedFlight = flightOffers[index];
  if (!selectedFlight || selectedFlight.bookable === false || selectedFlight.price == null) {
    showFlightError("This result is informational and has no bookable supplier fare. Use Duffel for priced flight selection and booking.");
    return;
  }
  const instantPaymentRequired = Boolean(selectedFlight.payment_requirements?.requires_instant_payment);
  const holdAllowed = !instantPaymentRequired;
  const bookingNote = instantPaymentRequired
    ? "This offer requires payment at booking time. Trust Trip MAS will use Razorpay Checkout before supplier fulfilment."
    : "This offer supports either secure Razorpay payment now or a provider hold in sandbox mode.";
  document.getElementById("flightBookingSummary").innerHTML = `<strong>${esc(selectedFlight.owner || "Flight")}</strong> · ${esc(selectedFlight.segments?.[0]?.origin || "")} → ${esc(selectedFlight.segments?.[selectedFlight.segments.length-1]?.destination || "")} · <strong>${esc(selectedFlight.currency || "")} ${Number(selectedFlight.price || 0).toFixed(2)}</strong><br><small>${esc(bookingNote)}</small>`;
  const passengerCount = passengerCountFromOffer(selectedFlight);
  document.getElementById("passengerForm").innerHTML = Array.from({length: passengerCount}, (_, i) => {
    const n = i + 1;
    return `<fieldset class="traveller-group">
      <legend>Traveller ${n} information</legend>
      <div class="passenger-fields">
        <select id="passTitle_${n}" required><option value="">Title</option><option value="mr">Mr</option><option value="mrs">Mrs</option><option value="ms">Ms</option><option value="miss">Miss</option><option value="mx">Mx</option><option value="dr">Dr</option></select>
        <select id="passGender_${n}" required><option value="">Gender</option><option value="m">Male</option><option value="f">Female</option><option value="x">X / Other</option></select>
        <input id="passGiven_${n}" maxlength="40" placeholder="Given name (max 40 characters)" required>
        <input id="passFamily_${n}" maxlength="40" placeholder="Family name (max 40 characters)" required>
        <input id="passBorn_${n}" type="date" required>
        <input id="passEmail_${n}" type="email" placeholder="Email">
        <input id="passPhone_${n}" placeholder="Phone number (+91 9876543210)">
      </div>
    </fieldset>`;
  }).join("");
  document.getElementById("passengerForm").insertAdjacentHTML("afterbegin", `<div class="traveller-count-note"><strong>${passengerCount} traveller${passengerCount === 1 ? "" : "s"}</strong> — enter separate details for every traveller before payment.</div>`);
  const choice = document.getElementById("flightPaymentMode");
  const holdOption = choice?.querySelector('option[value="hold"]');
  const sandboxOption = choice?.querySelector('option[value="sandbox_balance"]');
  if (holdOption) {
    holdOption.disabled = !holdAllowed;
    holdOption.textContent = holdAllowed ? "Hold now — pay later (sandbox)" : "Hold now — unavailable for this offer";
  }
  if (choice) {
    // Never silently switch a Razorpay selection back to Duffel balance.
    if (choice.querySelector('option[value="razorpay"]')) choice.value = "razorpay";
    else choice.value = instantPaymentRequired ? "sandbox_balance" : "hold";
  }
  if (sandboxOption) sandboxOption.textContent = "Pay now — Duffel Sandbox Balance (demo)";
  document.getElementById("paymentChoice").classList.remove("hidden");
  document.querySelector("#flightBookingSection .approval-actions")?.classList.remove("hidden");
  const finalBtn = document.getElementById("finalBookBtn");
  if (finalBtn) { finalBtn.disabled = false; finalBtn.textContent = "Review & Pay / Book"; }
  document.getElementById("flightBookingSection").classList.remove("hidden");
  document.getElementById("flightBookingSection").scrollIntoView({behavior:"smooth", block:"center"});
}

async function confirmFlightBooking() {
  if (!selectedFlight?.id) return showFlightError("Please select a flight first.");
  const passengerCount = passengerCountFromOffer(selectedFlight);
  const passengers = [];
  for (let i = 1; i <= passengerCount; i++) {
    const title = document.getElementById(`passTitle_${i}`)?.value || "";
    const gender = document.getElementById(`passGender_${i}`)?.value || "";
    const given = document.getElementById(`passGiven_${i}`)?.value.trim() || "";
    const family = document.getElementById(`passFamily_${i}`)?.value.trim() || "";
    const born = document.getElementById(`passBorn_${i}`)?.value || "";
    const email = document.getElementById(`passEmail_${i}`)?.value.trim() || "";
    const phone = document.getElementById(`passPhone_${i}`)?.value.trim() || "";
    const validName = /^[A-Za-zÀ-ÖØ-öø-ÿĀ-ž'\- ]+$/.test(given) && /^[A-Za-zÀ-ÖØ-öø-ÿĀ-ž'\- ]+$/.test(family);
    if (!title || !gender || !given || !family || !born) return showFlightError(`Title, gender, given name, family name and date of birth are required for Traveller ${i}.`);
    if (given.length > 40 || family.length > 40) return showFlightError(`Traveller ${i}: given name and family name must each be 40 characters or fewer.`);
    if (!validName) return showFlightError(`Traveller ${i}: names may contain only letters, spaces, hyphens, and apostrophes.`);
    let normalizedPhone = phone.replace(/[\s\-()]/g, "");
    if (/^\d{10}$/.test(normalizedPhone)) normalizedPhone = "+91" + normalizedPhone;
    if (normalizedPhone.startsWith("00")) normalizedPhone = "+" + normalizedPhone.slice(2);
    if (normalizedPhone && !/^\+\d{8,15}$/.test(normalizedPhone)) return showFlightError(`Traveller ${i}: phone number must be international format, e.g. +91 9876543210, or leave it blank.`);
    passengers.push({title, gender, given_name:given, family_name:family, born_on:born, email:email || undefined, phone_number:normalizedPhone || undefined, type:"adult"});
  }

  const primary = passengers[0];
  const paymentMode = document.getElementById("flightPaymentMode")?.value || "razorpay";
  if (paymentMode === "razorpay" && typeof Razorpay === "undefined") return showFlightError("Razorpay Checkout could not be loaded. Check your internet connection and reload the page.");
  const ok = window.confirm("FINAL BOOKING CONFIRMATION\n\nTrust Trip MAS will recheck the live fare, create a secure payment order, and only after successful payment attempt the airline booking.\n\nContinue?");
  if (!ok) return;

  const btn = document.getElementById("finalBookBtn");
  btn.disabled = true;
  btn.textContent = "Rechecking fare...";
  hideFlightError();

  try {
    // Duffel permits only ONE order per offer request. A second booking from
    // the same search must therefore create a fresh offer request first.
    // The backend matches the same itinerary and returns a new offer ID.
    const refreshResp = await fetch("/api/flights/refresh-selected-offer", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({offer_id:selectedFlight.id, search_context:selectedFlight.search_context || null})});
    const refreshed = await refreshResp.json();
    if (!refreshResp.ok || !refreshed.success) throw new Error(paymentErrorMessage(refreshed, "The selected flight is no longer available. Please choose a current offer."));
    const latest = refreshed.offer;
    const oldPassengerCount = passengerCountFromOffer(selectedFlight);
    const latestPassengerCount = passengerCountFromOffer(latest);
    if (oldPassengerCount !== latestPassengerCount || latestPassengerCount !== passengers.length) {
      throw new Error(`The refreshed supplier offer requires ${latestPassengerCount} traveller(s), but ${passengers.length} traveller(s) were entered. Please select the refreshed offer and enter traveller details again.`);
    }
    const oldPrice = Number(selectedFlight.price);
    const newPrice = Number(latest.price);
    // From this point onward every payment/order operation uses the fresh offer.
    latest.search_context = selectedFlight.search_context || latest.search_context || null;
    selectedFlight = latest;
    if (Number.isFinite(oldPrice) && Number.isFinite(newPrice) && Math.abs(oldPrice-newPrice) > 0.01) {
      const accept = window.confirm(`The live fare changed from ${selectedFlight.currency} ${oldPrice.toFixed(2)} to ${latest.currency} ${newPrice.toFixed(2)}.\n\nFINAL PRICE CONFIRMATION: ${latest.currency} ${newPrice.toFixed(2)}\n\nContinue?`);
      if (!accept) { btn.disabled=false; btn.textContent="Review & Pay / Book"; return; }
    }

    if (paymentMode === "razorpay") {
      btn.textContent = "Creating secure payment...";
      const orderResp = await fetch("/api/payments/create-order", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
          offer_id:selectedFlight.id,
          expected_amount:newPrice,
          currency:String(latest.currency || selectedFlight.currency || "").toUpperCase(),
          passengers,
          customer_ref:getCustomerRef(),
          final_confirmation:true
        })
      });
      const orderData = await orderResp.json();
      if (!orderResp.ok || !orderData.success) throw new Error(paymentErrorMessage(orderData, `Could not create the payment order (HTTP ${orderResp.status}).`));

      const pricing = orderData.pricing || {};
      const providerAmount = Number(pricing.provider_amount ?? newPrice);
      const customerAmount = Number(pricing.customer_amount ?? (Number(orderData.amount || 0) / 100));
      const providerCurrency = pricing.provider_currency || latest.currency || selectedFlight.currency || "";
      const customerCurrency = pricing.customer_currency || orderData.currency || "INR";
      const rateText = pricing.fx_rate ? `1 ${providerCurrency} = ${Number(pricing.fx_rate).toFixed(4)} ${customerCurrency}` : "live reference rate";
      const paymentConfirm = window.confirm(`FINAL PAYMENT CONFIRMATION\n\nFlight fare: ${providerCurrency} ${providerAmount.toFixed(2)}\nCustomer payment: ${customerCurrency} ${customerAmount.toFixed(2)}\nExchange rate: ${rateText}\n\nTrust Trip MAS will charge the INR amount shown above through Razorpay and then fulfil the flight with the supplier.\n\nContinue to Razorpay Checkout?`);
      if (!paymentConfirm) { btn.disabled=false; btn.textContent="Review & Pay / Book"; return; }

      await new Promise((resolve, reject) => {
        const options = {
          key: orderData.key_id,
          amount: orderData.amount,
          currency: orderData.currency,
          name: "Trust Trip MAS",
          description: `Flight booking — ${providerCurrency} ${providerAmount.toFixed(2)}`,
          order_id: orderData.order_id,
          prefill: {name: `${primary.given_name} ${primary.family_name}`, email: primary.email || "", contact: primary.phone_number || ""},
          notes: {transaction_id: orderData.transaction_id, offer_id: selectedFlight.id, passenger_count: String(passengers.length)},
          theme: {color: "#2563eb"},
          handler: async function(response) {
            try {
              btn.textContent = window.TRUST_TRIP_PRESENTATION_MODE ? "Verifying TEST payment and booking..." : "Verifying payment and booking...";
              const bookingEndpoint = window.TRUST_TRIP_PRESENTATION_MODE ? "/api/payments/presentation-book" : "/api/payments/verify-and-book";
              const verifyResp = await fetch(bookingEndpoint, {
                method:"POST", headers:{"Content-Type":"application/json"},
                body:JSON.stringify({
                  transaction_id:orderData.transaction_id,
                  razorpay_order_id:response.razorpay_order_id,
                  razorpay_payment_id:response.razorpay_payment_id,
                  razorpay_signature:response.razorpay_signature,
                  final_confirmation:true
                })
              });
              const verifyData = await verifyResp.json();
              if (!verifyResp.ok || (!verifyData.success && !verifyData.booking_pending)) throw new Error(paymentErrorMessage(verifyData, "Payment verification/flight booking failed."));
              let booking = verifyData.booking || {};
              if (verifyData.booking_pending) {
                document.getElementById("flightBookingSummary").innerHTML = `<strong>✓ Payment captured — airline PNR is being confirmed.</strong><br>Supplier order: <strong>${esc(booking.order_id || "Pending")}</strong><br><small>The page remains usable while the supplier confirms the existing order. No duplicate booking will be attempted.</small>`;
                for (let i = 0; i < 15; i++) {
                  await new Promise(r => setTimeout(r, 2000));
                  const refreshResp = await fetch(`/api/trips/${encodeURIComponent(orderData.transaction_id)}/refresh?customer_ref=${encodeURIComponent(getCustomerRef())}`, {method:"POST"});
                  const refresh = await refreshResp.json();
                  if (refresh.success && refresh.status === "booked") { booking = {order_id: refresh.duffel_order_id, booking_reference: refresh.booking_reference}; break; }
                }
              }
              if (!booking.booking_reference) {
                document.getElementById("flightBookingSummary").innerHTML = `<strong>Payment captured — booking confirmation is pending.</strong><br><small>The supplier order exists. Open My Trips to refresh the PNR; do not pay again.</small>`;
                document.getElementById("paymentChoice").classList.add("hidden");
                document.querySelector("#flightBookingSection .approval-actions")?.classList.add("hidden");
                await loadTrips(); resolve(); return;
              }
              document.getElementById("flightBookingSummary").innerHTML = `<div class="flight-success-state"><strong>✓ Flight ticket confirmed</strong><br>PNR: <strong>${esc(booking.booking_reference)}</strong><br>Supplier order: ${esc(booking.order_id || "—")}<br>Travellers: <strong>${passengers.length}</strong><br><small>Payment successful. The confirmed ticket is available in My Trips.</small></div>`;
              document.getElementById("paymentChoice").classList.add("hidden");
              document.querySelector("#flightBookingSection .approval-actions")?.classList.add("hidden");
              document.getElementById("passengerForm").innerHTML = "";
              await loadTrips();
              resolve();
            } catch (e) { reject(e); }
          },
          modal: {ondismiss: () => reject(new Error("Payment window was closed before completion."))}
        };
        const checkout = new Razorpay(options);
        checkout.on("payment.failed", failure => reject(new Error(failure.error?.description || "Razorpay payment failed.")));
        checkout.open();
      });
    } else {
      btn.textContent = paymentMode === "sandbox_balance" ? "Processing sandbox booking..." : "Creating hold order...";
      const payload = {offer_id:selectedFlight.id, expected_amount:newPrice, final_confirmation:true, allow_price_change:false, payment_mode:paymentMode, passengers};
      const response = await fetch("/api/flights/book", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
      const data = await response.json();
      if (!response.ok || !data.success) throw new Error(paymentErrorMessage(data, `Booking failed (HTTP ${response.status}).`));
      const booking = data.booking || {};
      const isHold = booking.order_type === "hold" || booking.status === "hold";
      const headline = isHold ? "Hold order created — payment still required." : "Sandbox booking created successfully.";
      const payButton = isHold && booking.order_id ? `<button class="approve-btn" onclick="payExistingHold('${esc(booking.order_id)}')">Pay Hold in Sandbox</button>` : "";
      document.getElementById("flightBookingSummary").innerHTML = `<strong>${headline}</strong><br>Reference: <strong>${esc(booking.booking_reference || "Pending")}</strong><br>Order: ${esc(booking.order_id || "—")}<br><small>${booking.live_mode ? "Live booking" : "Sandbox/test order"}. ${isHold ? `Payment required by ${esc(flightTime(booking.payment_required_by || "the provider deadline"))}` : "No real money was charged."}</small><div class="approval-actions" style="margin-top:12px">${payButton}</div>`;
      document.getElementById("paymentChoice").classList.add("hidden");
      if (!isHold) document.querySelector("#flightBookingSection .approval-actions")?.classList.add("hidden");
      await loadTrips();
    }
    btn.disabled = false;
    btn.textContent = "Review & Pay / Book";
    selectedFlight = null;
  } catch (error) {
    showFlightError(error.message || String(error));
    btn.disabled=false;
    btn.textContent="Review & Pay / Book";
  }
}

async function payExistingHold(orderId) {
  const ok = window.confirm("FINAL PAYMENT CONFIRMATION\n\nPay this hold order using the Duffel TEST/SANDBOX balance?\n\nNo real money will be charged. Continue?");
  if (!ok) return;
  hideFlightError();
  try {
    const response = await fetch("/api/flights/pay", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({order_id:orderId, final_confirmation:true})});
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error || data.detail || `Payment failed (HTTP ${response.status}).`);
    document.getElementById("flightBookingSummary").innerHTML = `<strong>Sandbox payment successful.</strong><br>Order: <strong>${esc(data.order_id || orderId)}</strong><br>Payment: <strong>${esc(data.payment_id || "Created")}</strong><br><small>Status: ${esc(data.payment_status || "succeeded")}. No real money was charged.</small>`;
  } catch (error) {
    showFlightError(error.message);
  }
}

async function initializePaymentUI() {
  try {
    const response = await fetch("/api/payments/config");
    const data = await response.json();
    window.TRUST_TRIP_PRESENTATION_MODE = Boolean(data.presentation_mode);
    const select = document.getElementById("flightPaymentMode");
    const choice = document.getElementById("paymentChoice");
    if (!select || !choice) return;
    if (data.enabled) {
      select.innerHTML = '<option value="razorpay">Pay securely with Razorpay</option><option value="sandbox_balance">Use Duffel sandbox balance (demo)</option><option value="hold">Hold now — pay later (sandbox)</option>';
      select.value = "razorpay";
      choice.querySelector("small").textContent = "✓ Razorpay Checkout is configured. UPI/card/netbanking methods are presented by Razorpay. Trust Trip MAS never receives raw payment credentials and verifies the payment signature server-side before booking.";
    } else {
      select.innerHTML = '<option value="sandbox_balance">Duffel Sandbox Balance (demo)</option><option value="hold">Hold now — pay later (sandbox)</option>';
      choice.querySelector("small").textContent = "Razorpay is not configured on this deployment. Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET to .env for the real Checkout flow.";
    }
  } catch (_) {}
}

async function loadBookedHotels() {
  try {
    const response = await fetch(`/api/trips?customer_ref=${encodeURIComponent(getCustomerRef())}`);
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.detail || "Could not load booked hotels.");
    const list = document.getElementById("bookedHotelsList");
    const hotels = (data.trips || []).filter(tx => tx.record_type === "hotel");
    if (!hotels.length) { list.innerHTML = '<div class="empty-state">No hotel booking records yet.</div>'; return; }
    list.innerHTML = hotels.map(tx => {
      const status = String(tx.booking_status || "pending").toLowerCase();
      const payment = String(tx.payment_status || "pending");
      const booking = tx.booking_json || {};
      const ref = tx.booking_reference || booking.confirmation_code || booking.booking_id || "Pending transaction";
      const amount = tx.amount != null ? `INR ${Number(tx.amount).toFixed(2)}` : "—";
      const pdf = status === "booked" ? `<a class="revise-btn" href="/api/trips/${encodeURIComponent(tx.id)}/hotel.pdf?customer_ref=${encodeURIComponent(getCustomerRef())}" target="_blank" rel="noopener">Download Hotel PDF</a>` : "";
      return `<article class="trip-row ${["pending","awaiting_payment","provider_pending","booking_pending","booking_in_progress"].includes(status) ? "trip-row-pending" : ""}">
        <div class="trip-main"><div class="trip-title-line"><strong>🏨 ${esc(tx.hotel_name || "Hotel booking")}</strong><span class="trip-type-badge">Hotel</span></div>
        <small>${esc(ref)} · Payment: ${esc(payment)} · Booking: ${esc(status)}${tx.error ? ` · ${esc(tx.error)}` : ""}</small></div>
        <div class="trip-actions"><span>${amount}</span>${pdf}<button type="button" class="delete-trip-btn" onclick="deleteTripRecord('${esc(tx.id)}','hotel')" title="Delete this hotel record">Delete</button></div>
      </article>`;
    }).join("");
  } catch (e) { console.warn(e); }
}

function toggleBookedHotels() {
  const dropdown=document.getElementById("bookedHotelsDropdown"); const button=document.getElementById("bookedHotelsMenuBtn"); if(!dropdown||!button)return;
  const isOpen=!dropdown.classList.contains("hidden"); dropdown.classList.toggle("hidden",isOpen); button.setAttribute("aria-expanded",String(!isOpen)); button.title=isOpen?"Open Booked Hotels":"Close Booked Hotels"; if(!isOpen)loadBookedHotels();
}

async function loadTrips() {
  try {
    const response = await fetch(`/api/trips?customer_ref=${encodeURIComponent(getCustomerRef())}`);
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.detail || "Could not load trips.");
    const list = document.getElementById("tripsList");
    if (!data.trips?.length) {
      list.innerHTML = '<div class="empty-state">No ticket or booking records yet.</div>';
      return;
    }
    list.innerHTML = data.trips.map(tx => {
      const type = tx.record_type === "hotel" ? "Hotel" : "Flight";
      const status = tx.booking_status || "pending";
      const payment = tx.payment_status || "pending";
      const ref = tx.booking_reference || tx.duffel_order_id || tx.razorpay_order_id || "Pending transaction";
      const title = tx.record_type === "hotel" ? (tx.hotel_name || "Hotel booking") : ref;
      const refreshButton = tx.record_type === "flight" && ["provider_pending", "booking_pending"].includes(status) && tx.duffel_order_id ? `<button class="revise-btn" onclick="refreshTrip('${esc(tx.id)}')">Refresh PNR</button>` : "";
      const ticketButton = tx.record_type === "flight" && status === "booked" && tx.duffel_order_id ? `<a class="revise-btn" href="/api/trips/${encodeURIComponent(tx.id)}/ticket.pdf?customer_ref=${encodeURIComponent(getCustomerRef())}" target="_blank" rel="noopener">Download Ticket PDF</a>` : "";
      const cancelButton = "";
      const refundButton = tx.record_type === "flight" && ["booking_failed", "booking_ambiguous"].includes(status) && tx.razorpay_payment_id && !["processed", "processed_partial"].includes(tx.refund_status) ? `<button class="revise-btn" onclick="requestRefund('${esc(tx.id)}')">Request Payment Refund</button>` : "";
      const amount = tx.amount != null ? `${esc(tx.currency || "INR")} ${Number(tx.amount).toFixed(2)}` : "—";
      return `<article class="trip-row ${status === "pending" || status === "awaiting_payment" || status === "provider_pending" || status === "booking_pending" ? "trip-row-pending" : ""}">
        <div class="trip-main"><div class="trip-title-line"><strong>${esc(title)}</strong><span class="trip-type-badge">${type}</span></div>
        <small>${esc(ref)} · Payment: ${esc(payment)} · Booking: ${esc(status)}${tx.refund_status ? ` · Refund: ${esc(tx.refund_status)}` : ""}</small></div>
        <div class="trip-actions"><span>${amount}</span>${refreshButton}${ticketButton}${cancelButton}${refundButton}<button type="button" class="delete-trip-btn" onclick="deleteTripRecord('${esc(tx.id)}','${tx.record_type}')" title="Delete this record">Delete</button></div>
      </article>`;
    }).join("");
  } catch (e) { console.warn(e); }
}

function toggleTrips() {
  const dropdown = document.getElementById("tripsDropdown");
  const button = document.getElementById("tripsMenuBtn");
  const isOpen = !dropdown.classList.contains("hidden");
  dropdown.classList.toggle("hidden", isOpen);
  button.setAttribute("aria-expanded", String(!isOpen));
  button.title = isOpen ? "Open My Trips" : "Close My Trips";
  if (!isOpen) loadTrips();
}

async function deleteTripRecord(transactionId, recordType) {
  try {
    const response = await fetch(`/api/trips/${encodeURIComponent(transactionId)}?record_type=${encodeURIComponent(recordType)}&customer_ref=${encodeURIComponent(getCustomerRef())}`, { method: "DELETE" });
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.detail || "Could not delete this trip record.");
    await loadTrips();
  } catch (e) {
    alert(e.message || "Could not delete this trip record.");
  }
}


async function refreshTrip(transactionId) {
  try {
    const response = await fetch(`/api/trips/${encodeURIComponent(transactionId)}/refresh?customer_ref=${encodeURIComponent(getCustomerRef())}`, {method:"POST"});
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.detail || "Could not refresh the supplier booking.");
    if (data.status === "booked") alert(`PNR confirmed: ${data.booking_reference}. Your ticket PDF is now available in My Trips.`);
    else alert(data.message || "The airline has not returned the PNR yet. Please refresh again shortly.");
    await loadTrips();
  } catch (e) { showFlightError(e.message || String(e)); }
}


async function requestRefund(transactionId) {
  const ok = window.confirm("FINAL REFUND CONFIRMATION\n\nThe airline booking was not completed. Trust Trip MAS will request a refund of the captured Razorpay payment. Continue?");
  if (!ok) return;
  try {
    const response = await fetch(`/api/payments/refund?transaction_id=${encodeURIComponent(transactionId)}&final_confirmation=true`, {method:"POST"});
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(paymentErrorMessage(data, "Refund could not be initiated."));
    alert(`Refund request created. Status: ${data.refund?.status || "created"}.`);
    await loadTrips();
  } catch (e) { showFlightError(e.message || String(e)); }
}

async function requestCancellation(transactionId) {
  const ok = window.confirm("FINAL CANCELLATION REQUEST\n\nTrust Trip MAS will retrieve the airline cancellation quote first. Continue?");
  if (!ok) return;
  try {
    const response = await fetch("/api/trips/cancel/quote", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({transaction_id:transactionId, final_confirmation:true})});
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.detail || "Could not retrieve the cancellation quote.");
    const quote = data.quote || {};
    const amount = quote.refund_amount || "0";
    const confirm = window.confirm(`Cancellation quote\n\nPotential airline refund: ${quote.refund_currency || ""} ${amount}\nRefund destination: ${quote.refund_to || "provider"}\n\nConfirm cancellation?`);
    if (!confirm) return;
    const confirmResp = await fetch("/api/trips/cancel/confirm", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({transaction_id:transactionId, cancellation_id:quote.id, final_confirmation:true})});
    const result = await confirmResp.json();
    if (!confirmResp.ok || !result.success) throw new Error(result.detail || result.refund_error || "Cancellation failed.");
    alert(result.refund ? `Flight cancelled. Customer refund: ${result.refund.status || "initiated"}.` : "Flight cancelled. Any customer refund requiring manual review is shown in the transaction status.");
    await loadTrips();
  } catch (e) { showFlightError(e.message); }
}

function cancelFlightBooking() { selectedFlight=null; document.getElementById("flightBookingSection").classList.add("hidden"); }

window.addEventListener("DOMContentLoaded", () => { initializePaymentUI(); });
