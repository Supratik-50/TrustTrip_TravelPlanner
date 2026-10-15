from __future__ import annotations

from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether,
)


def build_travel_plan_pdf(plan: dict[str, Any]) -> bytes:
    """Build a real text PDF from structured plan data; never depends on browser rendering."""
    if not isinstance(plan, dict) or not plan:
        raise ValueError("No structured travel plan is available for PDF export.")

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=13 * mm, leftMargin=13 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title="Trust Trip MAS — Travel Plan",
        author="Trust Trip MAS",
    )
    try:
        pdfmetrics.registerFont(TTFont("TTDejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
        pdfmetrics.registerFont(TTFont("TTDejaVuBold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
    except Exception:
        pass
    font = "TTDejaVu" if "TTDejaVu" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Brand", parent=styles["Normal"], fontSize=8.5, leading=10, textColor=colors.HexColor("#2563eb"), spaceAfter=2, fontName=font))
    styles.add(ParagraphStyle(name="TitleTT", parent=styles["Title"], fontSize=19, leading=22, textColor=colors.HexColor("#0f172a"), spaceAfter=4, fontName=font))
    styles.add(ParagraphStyle(name="SubTT", parent=styles["Normal"], fontSize=8.5, leading=11, textColor=colors.HexColor("#64748b"), spaceAfter=8, fontName=font))
    styles.add(ParagraphStyle(name="H2TT", parent=styles["Heading2"], fontSize=11.5, leading=14, textColor=colors.HexColor("#0f172a"), spaceBefore=7, spaceAfter=5, fontName=font))
    styles.add(ParagraphStyle(name="H3TT", parent=styles["Heading3"], fontSize=9.5, leading=12, textColor=colors.HexColor("#0f172a"), spaceAfter=3, fontName=font))
    styles.add(ParagraphStyle(name="BodyTT", parent=styles["BodyText"], fontSize=8.3, leading=11.2, textColor=colors.HexColor("#334155"), spaceAfter=3, fontName=font))
    styles.add(ParagraphStyle(name="SmallTT", parent=styles["BodyText"], fontSize=7.5, leading=9.5, textColor=colors.HexColor("#64748b"), spaceAfter=2, fontName=font))
    styles.add(ParagraphStyle(name="FactTT", parent=styles["BodyText"], fontSize=8.4, leading=11.3, textColor=colors.HexColor("#111827"), spaceAfter=3, fontName=font))

    def esc(v: Any) -> str:
        s = str(v if v is not None else "")
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    story = []
    route = esc(plan.get("route") or plan.get("title") or "Travel Plan")
    story += [Paragraph("TRUST TRIP MAS", styles["Brand"]), Paragraph(route, styles["TitleTT"])]
    dates_str = plan.get("travel_dates")
    if not dates_str or dates_str == "Flexible dates":
        dep = plan.get("departure_date") or plan.get("start_date")
        ret = plan.get("return_date") or plan.get("end_date")
        if dep and ret:
            dates_str = f"{dep} to {ret}"
        elif dep:
            dates_str = f"From {dep}"
        else:
            dates_str = "Flexible dates"
    duration = plan.get("duration_days") or 1
    style = plan.get("travel_style") or "Balanced"
    story.append(Paragraph(
        f"{esc(dates_str)} · {esc(duration)} days · {esc(style)}",
        styles["SubTT"],
    ))
    story.append(Paragraph(esc(plan.get("trip_summary") or "Evidence-grounded travel plan."), styles["BodyTT"]))

    # Recommendations in a compact, readable 3-column table.
    story.append(Paragraph("Key Recommendations & Confirmed Stays", styles["H2TT"]))
    flight_label = "✈ Confirmed Flight" if plan.get("confirmed_flight") else "✈ Flight Recommendation"
    flight_val = plan.get("confirmed_flight") or plan.get("flight_suggestion") or "No live flight recommendation."

    hotel_label = "🏨 Confirmed Hotel" if plan.get("confirmed_hotel") else "🏨 Accommodation"
    hotel_val = plan.get("confirmed_hotel") or plan.get("accommodation_plan") or "No live accommodation recommendation."

    weather_label = "🌦 Weather Guidance"
    weather_val = plan.get("weather_guidance") or "Weather guidance unavailable."

    cards = [
        (flight_label, flight_val),
        (hotel_label, hotel_val),
        (weather_label, weather_val),
    ]
    data = [[Paragraph(f"<b>{esc(h)}</b>", styles["H3TT"]) for h, _ in cards], [Paragraph(esc(v).replace("**", ""), styles["FactTT"]) for _, v in cards]]
    table = Table(data, colWidths=[(A4[0]-26*mm)/3]*3, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#f8fafc")),
        ("BOX", (0,0), (-1,-1), 0.6, colors.HexColor("#dbe4f0")),
        ("INNERGRID", (0,0), (-1,-1), 0.4, colors.HexColor("#e2e8f0")),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 6), ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 6), ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))
    story += [table, Spacer(1, 4)]

    # Budget section: truthfully reflect authoritative saved data only; no misleading zeros
    story.append(Paragraph("Budget", styles["H2TT"]))
    b = plan.get("budget") if isinstance(plan.get("budget"), dict) else {}
    budget_rows: list[list[str]] = []

    if plan.get("confirmed_flight_cost") is not None:
        budget_rows.append(["Confirmed flight", f"₹{float(plan['confirmed_flight_cost']):,.0f}"])
    elif b.get("flight_estimate_inr") is not None and float(b.get("flight_estimate_inr", 0)) > 0:
        budget_rows.append(["Flight estimate", f"₹{float(b['flight_estimate_inr']):,.0f}"])

    if plan.get("confirmed_hotel_cost") is not None:
        budget_rows.append(["Confirmed hotel", f"₹{float(plan['confirmed_hotel_cost']):,.0f}"])
    elif b.get("hotel_estimate_inr") is not None and float(b.get("hotel_estimate_inr", 0)) > 0:
        budget_rows.append(["Hotel estimate", f"₹{float(b['hotel_estimate_inr']):,.0f}"])

    if b.get("local_transport_estimate_inr") is not None and float(b.get("local_transport_estimate_inr", 0)) > 0:
        budget_rows.append(["Local transport", f"₹{float(b['local_transport_estimate_inr']):,.0f}"])

    if b.get("activities_estimate_inr") is not None and float(b.get("activities_estimate_inr", 0)) > 0:
        budget_rows.append(["Activities", f"₹{float(b['activities_estimate_inr']):,.0f}"])

    if b.get("contingency_inr") is not None and float(b.get("contingency_inr", 0)) > 0:
        budget_rows.append(["Contingency", f"₹{float(b['contingency_inr']):,.0f}"])

    if b.get("estimated_total_inr") is not None and float(b.get("estimated_total_inr", 0)) > 0:
        budget_rows.append(["Estimated total", f"₹{float(b['estimated_total_inr']):,.0f}"])
    elif len(budget_rows) > 1:
        sum_tracked = sum([
            float(plan.get("confirmed_flight_cost") or b.get("flight_estimate_inr") or 0),
            float(plan.get("confirmed_hotel_cost") or b.get("hotel_estimate_inr") or 0),
            float(b.get("local_transport_estimate_inr") or 0),
            float(b.get("activities_estimate_inr") or 0),
            float(b.get("contingency_inr") or 0),
        ])
        if sum_tracked > 0:
            budget_rows.append(["Tracked subtotal", f"₹{sum_tracked:,.0f}"])

    if budget_rows:
        bt = Table([[Paragraph(f"<b>{esc(a)}</b>", styles["SmallTT"]), Paragraph(f"<b>{esc(c)}</b>", styles["FactTT"])] for a,c in budget_rows], colWidths=[55*mm, 45*mm], hAlign="LEFT")
        bt.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-2), colors.white),
            ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#eff6ff")),
            ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#dbe4f0")),
            ("INNERGRID", (0,0), (-1,-1), 0.3, colors.HexColor("#e2e8f0")),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("LEFTPADDING", (0,0), (-1,-1), 5), ("RIGHTPADDING", (0,0), (-1,-1), 5),
            ("TOPPADDING", (0,0), (-1,-1), 3), ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ]))
        story.append(bt)
    elif b.get("target_budget_inr") is not None and float(b["target_budget_inr"]) > 0:
        story.append(Paragraph(
            f"Budget target: <b>₹{float(b['target_budget_inr']):,.0f}</b> · Itemized breakdown not recorded.",
            styles["FactTT"],
        ))
    else:
        story.append(Paragraph("No itemized budget estimates or targets recorded for this travel plan.", styles["BodyTT"]))

    if b.get("target_budget_inr") is not None and budget_rows:
        story.append(Paragraph(
            f"Budget target: <b>₹{float(b['target_budget_inr']):,.0f}</b> · Status: <b>{'Within target' if b.get('within_budget') else 'Above target'}</b> · {esc(b.get('pricing_note') or '')}",
            styles["SmallTT"],
        ))

    story.append(Paragraph("Day-by-Day Itinerary", styles["H2TT"]))
    daily_items = plan.get("daily_plan") or plan.get("itinerary") or []
    if daily_items and isinstance(daily_items, list):
        for idx, day in enumerate(daily_items, start=1):
            if not isinstance(day, dict):
                continue
            day_num = day.get("day") or idx
            title = f"Day {esc(day_num)}: {esc(day.get('title') or '')}"
            body = [Paragraph(title, styles["H3TT"])]
            for label, key in (("Morning", "morning"), ("Afternoon", "afternoon"), ("Evening", "evening")):
                if day.get(key):
                    body.append(Paragraph(f"<b>{label}:</b> {esc(day.get(key))}", styles["BodyTT"]))
            if day.get("estimated_daily_cost_inr") is not None and float(day.get("estimated_daily_cost_inr", 0)) > 0:
                body.append(Paragraph(f"Estimated day cost: <b>₹{float(day['estimated_daily_cost_inr']):,.0f}</b>", styles["SmallTT"]))
            for note in day.get("notes") or []:
                body.append(Paragraph(f"• {esc(note)}", styles["SmallTT"]))
            story.append(KeepTogether(body))
    else:
        story.append(Paragraph("No day-by-day itinerary activities recorded for this travel plan revision.", styles["BodyTT"]))

    for heading, key in (("Changes From Feedback", "changes_from_feedback"), ("Assumptions", "assumptions"), ("Important Notes", "warnings")):
        values = plan.get(key) or []
        if values:
            story.append(Paragraph(heading, styles["H2TT"]))
            for value in values:
                story.append(Paragraph(f"• {esc(value)}", styles["BodyTT"]))

    story.append(Spacer(1, 5))
    story.append(Paragraph("Trust Trip MAS · Provider-backed prices and availability should be rechecked before payment or booking.", styles["SmallTT"]))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(font, 7)
        canvas.setFillColor(colors.HexColor("#94a3b8"))
        canvas.drawRightString(A4[0]-13*mm, 7*mm, f"Trust Trip MAS · Page {doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    data = buf.getvalue()
    if not data.startswith(b"%PDF"):
        raise RuntimeError("PDF generation did not produce a valid PDF document.")
    return data
