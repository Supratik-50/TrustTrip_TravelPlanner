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
    """Build a non-empty hotel booking confirmation PDF from a persisted transaction.

    Hardened against long text strings, multi-room/multi-guest expansion, long cancellation
    policies, itemized price breakdowns, pagination, and PDF structural integrity.
    """
    booking = record.get("booking_json") or {}
    offer = record.get("offer_json") or {}
    holder = record.get("holder_json") or {}
    guests = record.get("guests_json") or []
    provider = record.get("provider") or booking.get("provider") or offer.get("provider") or "Hotel provider"
    status = record.get("booking_status") or booking.get("status") or "pending"
    confirmation = booking.get("confirmation_code") or booking.get("booking_id") or "Pending"
    hotel_name = record.get("hotel_name") or offer.get("hotel_name") or "Hotel booking"

    styles = getSampleStyleSheet()
    title = ParagraphStyle("HotelTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=18, leading=22, spaceAfter=4, textColor=colors.HexColor("#0f172a"))
    subtitle = ParagraphStyle("HotelSubtitle", parent=styles["Normal"], fontSize=8.5, textColor=colors.HexColor("#475569"), leading=12)
    h2 = ParagraphStyle("HotelH2", parent=styles["Heading2"], fontSize=11, leading=14, spaceBefore=8, spaceAfter=5, textColor=colors.HexColor("#0f172a"))
    h3 = ParagraphStyle("HotelH3", parent=styles["Heading3"], fontSize=9, leading=12, textColor=colors.HexColor("#0f172a"))
    body = ParagraphStyle("HotelBody", parent=styles["BodyText"], fontSize=8.5, leading=11.5, textColor=colors.HexColor("#1e293b"))
    bold_label = ParagraphStyle("HotelBoldLabel", parent=body, fontName="Helvetica-Bold", fontSize=8.5, leading=11.5, textColor=colors.HexColor("#0f172a"))
    small = ParagraphStyle("HotelSmall", parent=body, fontSize=7.5, leading=10, textColor=colors.HexColor("#64748b"))

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title="Trust Trip Hotel Booking Confirmation",
        author="Trust Trip MAS",
    )

    story = [
        Paragraph("Trust Trip Travel Planner", title),
        Paragraph("Hotel Reservation & Booking Confirmation", h2),
        Paragraph("Official confirmation record generated from persisted booking transaction.", subtitle),
        Spacer(1, 4 * mm),
    ]

    # 1. Booking Details Table (wrapped in Paragraph flowables to prevent text clipping)
    booking_rows = [
        [Paragraph("Booking status", bold_label), Paragraph(f"<b>{escape(str(status).upper())}</b>", body)],
        [Paragraph("Confirmation Code", bold_label), Paragraph(f"<b>{escape(str(confirmation))}</b>", body)],
        [Paragraph("Supplier Provider", bold_label), Paragraph(escape(str(provider)), body)],
        [Paragraph("Hotel Name", bold_label), Paragraph(escape(str(hotel_name)), body)],
        [Paragraph("Room Description", bold_label), Paragraph(escape(str(offer.get("room_name") or "Standard Room")), body)],
        [Paragraph("Board Type", bold_label), Paragraph(escape(str(offer.get("board_name") or offer.get("board_type") or "Room Only")), body)],
        [Paragraph("Check-in Date", bold_label), Paragraph(escape(str(offer.get("checkin") or offer.get("check_in") or "—")), body)],
        [Paragraph("Check-out Date", bold_label), Paragraph(escape(str(offer.get("checkout") or offer.get("check_out") or "—")), body)],
        [Paragraph("Rooms Booked", bold_label), Paragraph(str(offer.get("rooms") or offer.get("rooms_count") or 1), body)],
    ]
    story.append(Paragraph("Reservation Details", h2))
    table = Table(booking_rows, colWidths=[45 * mm, 135 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(table)

    # 2. Lead Guest & Passenger Roster (dynamic expansion for multi-guest)
    lead_name = " ".join(x for x in [holder.get("firstName"), holder.get("lastName")] if x) or holder.get("name") or "—"
    guest_rows = [
        [Paragraph("Lead Guest Name", bold_label), Paragraph(escape(str(lead_name)), body)],
        [Paragraph("Contact Email", bold_label), Paragraph(escape(str(holder.get("email") or "—")), body)],
        [Paragraph("Contact Phone", bold_label), Paragraph(escape(str(holder.get("phone") or "—")), body)],
        [Paragraph("Total Guests", bold_label), Paragraph(str(len(guests) if guests else 1), body)],
    ]

    # Itemize additional guests if provided
    if guests:
        for idx, g in enumerate(guests, start=1):
            g_name = " ".join(x for x in [g.get("firstName") or g.get("first_name"), g.get("lastName") or g.get("last_name")] if x) or g.get("name") or f"Guest {idx}"
            g_type = "Child" if g.get("is_child") else "Adult"
            guest_rows.append([
                Paragraph(f"Guest {idx} ({g_type})", bold_label),
                Paragraph(escape(str(g_name)), body),
            ])

    story.append(Paragraph("Guest Information", h2))
    gt = Table(guest_rows, colWidths=[45 * mm, 135 * mm], hAlign="LEFT")
    gt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f8fafc")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(gt)

    # 3. Payment & Price Breakdown
    payment_rows = [
        [Paragraph("Total Amount Paid", bold_label), Paragraph(f"<b>{_fmt_money(record.get('amount_inr'), 'INR')}</b>", body)],
        [Paragraph("Payment Status", bold_label), Paragraph(escape(str(record.get("payment_status") or "—").upper()), body)],
        [Paragraph("Payment Gateway Order", bold_label), Paragraph(escape(str(record.get("razorpay_order_id") or "—")), small)],
        [Paragraph("Payment ID Reference", bold_label), Paragraph(escape(str(record.get("razorpay_payment_id") or "—")), small)],
        [Paragraph("Transaction ID", bold_label), Paragraph(escape(str(record.get("id") or "—")), small)],
        [Paragraph("Timestamp", bold_label), Paragraph(escape(str(record.get("created_at") or datetime.now())), small)],
    ]
    story.append(Paragraph("Payment & Financial Record", h2))
    pt = Table(payment_rows, colWidths=[45 * mm, 135 * mm], hAlign="LEFT")
    pt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(pt)

    # 4. Cancellation Policy & Special Conditions (Paginated defensively)
    cancellation_info = offer.get("cancellation") or {}
    cancellation_deadline = cancellation_info.get("deadline") or cancellation_info.get("cancellationDeadline") or offer.get("cancellation_deadline")
    policy_text = cancellation_info.get("policy") or cancellation_info.get("description") or (
        f"Free cancellation before {cancellation_deadline}." if cancellation_deadline else "Non-refundable booking."
    )
    story.append(Spacer(1, 3 * mm))
    policy_block = [
        Paragraph("Cancellation Terms & Policies", h3),
        Paragraph(escape(str(policy_text)), small),
    ]
    story.append(KeepTogether(policy_block))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(15 * mm, 7 * mm, "Trust Trip MAS — Verified Hotel Booking Confirmation")
        canvas.drawRightString(A4[0] - 15 * mm, 7 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    data = buf.getvalue()
    if not data.startswith(b"%PDF") or len(data) < 1000:
        raise ValueError("Hotel booking PDF generation produced an invalid or empty PDF.")
    return data
