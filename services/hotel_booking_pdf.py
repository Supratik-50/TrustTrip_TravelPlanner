from __future__ import annotations

from datetime import datetime
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
from xml.sax.saxutils import escape


def _fmt_money(value, currency="INR"):
    try:
        return f"{currency} {float(value):,.2f}"
    except Exception:
        return "—"


def build_hotel_booking_pdf(record: dict) -> bytes:
    """Build a non-empty hotel booking confirmation PDF from a persisted transaction."""
    booking = record.get("booking_json") or {}
    offer = record.get("offer_json") or {}
    holder = record.get("holder_json") or {}
    guests = record.get("guests_json") or []
    provider = record.get("provider") or booking.get("provider") or offer.get("provider") or "Hotel provider"
    status = record.get("booking_status") or booking.get("status") or "pending"
    confirmation = booking.get("confirmation_code") or booking.get("booking_id") or "Pending"
    hotel_name = record.get("hotel_name") or offer.get("hotel_name") or "Hotel booking"

    styles = getSampleStyleSheet()
    title = ParagraphStyle("HotelTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=20, leading=24, spaceAfter=5)
    subtitle = ParagraphStyle("HotelSubtitle", parent=styles["Normal"], fontSize=9.5, textColor=colors.HexColor("#475569"), leading=14)
    h2 = ParagraphStyle("HotelH2", parent=styles["Heading2"], fontSize=12, leading=15, spaceBefore=10, spaceAfter=6, textColor=colors.HexColor("#0f172a"))
    body = ParagraphStyle("HotelBody", parent=styles["BodyText"], fontSize=9, leading=13, textColor=colors.HexColor("#1e293b"))
    small = ParagraphStyle("HotelSmall", parent=body, fontSize=7.8, textColor=colors.HexColor("#64748b"))

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title="Trust Trip Hotel Booking Confirmation",
        author="Trust Trip MAS",
    )

    story = [
        Paragraph("Trust Trip", title),
        Paragraph("Hotel Booking Confirmation", h2),
        Paragraph("Generated from the persisted hotel booking transaction.", subtitle),
        Spacer(1, 7 * mm),
    ]

    booking_rows = [
        ["Booking status", str(status).upper()],
        ["Confirmation", str(confirmation)],
        ["Provider", str(provider)],
        ["Hotel", str(hotel_name)],
        ["Room", str(offer.get("room_name") or "Room")],
        ["Board", str(offer.get("board_name") or "Room only")],
        ["Check-in", str(offer.get("checkin") or offer.get("check_in") or "—")],
        ["Check-out", str(offer.get("checkout") or offer.get("check_out") or "—")],
        ["Rooms", str(offer.get("rooms") or len(guests) or 1)],
    ]
    story += [Paragraph("Booking details", h2)]
    table = Table(booking_rows, colWidths=[43 * mm, 130 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#0f172a")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [table]

    lead_name = " ".join(x for x in [holder.get("firstName"), holder.get("lastName")] if x) or holder.get("name") or "—"
    guest_rows = [["Lead guest", str(lead_name)], ["Email", str(holder.get("email") or "—")], ["Phone", str(holder.get("phone") or "—")], ["Guest records", str(len(guests) or 1)]]
    story += [Paragraph("Lead guest", h2)]
    gt = Table(guest_rows, colWidths=[43 * mm, 130 * mm], hAlign="LEFT")
    gt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f8fafc")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [gt]

    payment_rows = [
        ["Amount paid", _fmt_money(record.get("amount_inr"), "INR")],
        ["Payment status", str(record.get("payment_status") or "—").upper()],
        ["Razorpay order", str(record.get("razorpay_order_id") or "—")],
        ["Razorpay payment", str(record.get("razorpay_payment_id") or "—")],
        ["Booking transaction", str(record.get("id") or "—")],
        ["Created", str(record.get("created_at") or datetime.now())],
    ]
    story += [Paragraph("Payment", h2)]
    pt = Table(payment_rows, colWidths=[43 * mm, 130 * mm], hAlign="LEFT")
    pt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.2),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [pt]

    raw_note = booking.get("status") or "Provider booking response was saved with this transaction."
    story += [Spacer(1, 5 * mm), Paragraph(f"Provider status: {escape(str(raw_note))}", small)]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(16 * mm, 8 * mm, "Trust Trip MAS — Hotel booking confirmation")
        canvas.drawRightString(A4[0] - 16 * mm, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    data = buf.getvalue()
    if not data.startswith(b"%PDF") or len(data) < 1000:
        raise ValueError("Hotel booking PDF generation produced an invalid or empty PDF.")
    return data
