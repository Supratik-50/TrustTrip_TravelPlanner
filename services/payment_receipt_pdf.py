from __future__ import annotations

import io
from datetime import datetime
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether


def _fmt_money(value: Any, currency: str = "INR") -> str:
    try:
        return f"{currency} {float(value):,.2f}"
    except Exception:
        return "—"


def build_payment_receipt_pdf(record: dict[str, Any]) -> bytes:
    """Generate an authoritative, standalone payment receipt PDF for a Razorpay transaction.

    Includes verified merchant, transaction date, amount, currency, Razorpay
    order/payment IDs, and payment status.
    Truthfully distinguishes financial settlement from supplier reservation confirmation.
    Never invents PNRs, ticket numbers, hotel confirmation codes, or payment statuses.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title="TrustTrip Payment Receipt",
        author="TrustTrip MAS",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReceiptTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        spaceAfter=2,
        textColor=colors.HexColor("#0f172a"),
    )
    subtitle_style = ParagraphStyle(
        "ReceiptSubtitle",
        parent=styles["Normal"],
        fontSize=8.5,
        textColor=colors.HexColor("#475569"),
        leading=12,
    )
    h2_style = ParagraphStyle(
        "ReceiptH2",
        parent=styles["Heading2"],
        fontSize=11,
        leading=14,
        spaceBefore=8,
        spaceAfter=5,
        textColor=colors.HexColor("#0f172a"),
    )
    body_style = ParagraphStyle(
        "ReceiptBody",
        parent=styles["BodyText"],
        fontSize=8.5,
        leading=11.5,
        textColor=colors.HexColor("#1e293b"),
    )
    bold_label = ParagraphStyle(
        "ReceiptBoldLabel",
        parent=body_style,
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11.5,
        textColor=colors.HexColor("#0f172a"),
    )
    small_style = ParagraphStyle(
        "ReceiptSmall",
        parent=body_style,
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#64748b"),
    )
    code_style = ParagraphStyle(
        "ReceiptCode",
        parent=body_style,
        fontName="Courier",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#0f172a"),
    )

    tx_id = str(record.get("id") or record.get("transaction_id") or "—")
    merchant_name = str(record.get("merchant") or "TrustTrip Travel Planner (TrustTrip MAS)")
    payment_status = str(record.get("payment_status") or "PENDING").upper()
    booking_status = str(record.get("booking_status") or "PENDING").upper()
    amount = record.get("amount_inr") if record.get("amount_inr") is not None else record.get("amount")
    currency = str(record.get("currency") or "INR").upper()
    razorpay_order_id = str(record.get("razorpay_order_id") or "—")
    razorpay_payment_id = str(record.get("razorpay_payment_id") or "—")
    service_type = str(record.get("service_type") or "Travel Booking Service")
    service_item = str(record.get("service_name") or record.get("hotel_name") or record.get("item_description") or "Travel Reservation")
    customer_name = str(record.get("customer_name") or "—")
    customer_email = str(record.get("customer_email") or record.get("contact_email") or "—")
    created_at = str(record.get("created_at") or record.get("timestamp") or datetime.now().isoformat())

    # Truthful booking confirmation presentation
    confirmation_ref = record.get("confirmation_code") or record.get("booking_reference") or record.get("pnr")
    if booking_status == "BOOKED" or booking_status == "COMPLETED" or booking_status == "CONFIRMED":
        booking_display = f"CONFIRMED (Ref: {confirmation_ref})" if confirmation_ref else "CONFIRMED"
    elif booking_status in {"RECONCILIATION_PENDING", "RECONCILIATION_IN_PROGRESS", "BOOKING_IN_PROGRESS"}:
        booking_display = "PENDING SUPPLIER CONFIRMATION"
    elif "FAILED" in booking_status:
        booking_display = "SUPPLIER BOOKING DECLINED / FAILED"
    else:
        booking_display = booking_status

    has_rzp_payment = bool(
        razorpay_payment_id
        and str(razorpay_payment_id).strip() not in {"—", "-", "none", "null", "", "None"}
    )
    has_rzp_order = bool(
        razorpay_order_id
        and str(razorpay_order_id).strip() not in {"—", "-", "none", "null", "", "None"}
    )

    if has_rzp_payment:
        subtitle_text = "Official payment voucher verifying financial settlement via Razorpay."
        gateway_display = str(record.get("payment_gateway") or "Razorpay Payment Gateway (Secured)")
        payment_status_display = payment_status
        disclaimer_text = (
            "This receipt verifies financial settlement processed through Razorpay. "
            "A payment receipt does NOT constitute an airline boarding pass, flight e-ticket, or hotel check-in voucher. "
            "Supplier confirmation is issued separately upon confirmed reservation by the respective airline or hotel provider. "
            "If a supplier booking fails after payment capture, the amount is refunded or reconciled in accordance with TrustTrip policy."
        )
    elif has_rzp_order:
        subtitle_text = "Official payment order voucher (Payment Pending Capture)."
        gateway_display = str(record.get("payment_gateway") or "Razorpay Payment Gateway (Pending Capture)")
        payment_status_display = "ORDER CREATED (UNCAPTURED)" if payment_status in {"CAPTURED", "PENDING"} else payment_status
        disclaimer_text = (
            "This receipt documents an authorized payment order awaiting completion or capture. "
            "A payment receipt does NOT constitute an airline boarding pass, flight e-ticket, or hotel check-in voucher. "
            "Supplier confirmation is issued separately upon confirmed reservation by the respective airline or hotel provider."
        )
    else:
        subtitle_text = "Official booking receipt and supplier settlement statement."
        gateway_display = str(record.get("payment_gateway") or "Direct Provider Settlement")
        payment_status_display = "SETTLED DIRECT VIA SUPPLIER" if payment_status in {"CAPTURED", "SETTLED_DIRECT", "BOOKED", "COMPLETED"} else payment_status
        disclaimer_text = (
            "This receipt confirms supplier reservation and direct provider settlement without external payment gateway capture. "
            "A payment receipt does NOT constitute an airline boarding pass, flight e-ticket, or hotel check-in voucher. "
            "Supplier confirmation is issued separately upon confirmed reservation by the respective airline or hotel provider."
        )

    story = [
        Paragraph("TrustTrip Official Payment Receipt", title_style),
        Paragraph(f"Merchant: {escape(merchant_name)}", subtitle_style),
        Paragraph(subtitle_text, subtitle_style),
        Spacer(1, 4 * mm),
    ]

    # 1. Transaction & Gateway Information
    tx_rows = [
        [Paragraph("Transaction ID", bold_label), Paragraph(escape(tx_id), code_style)],
        [Paragraph("Transaction Date", bold_label), Paragraph(escape(created_at), body_style)],
        [Paragraph("Payment Status", bold_label), Paragraph(f"<b>{escape(payment_status_display)}</b>", body_style)],
    ]
    if has_rzp_payment:
        tx_rows.extend([
            [Paragraph("Payment Gateway", bold_label), Paragraph(escape(gateway_display), body_style)],
            [Paragraph("Razorpay Order ID", bold_label), Paragraph(escape(razorpay_order_id), code_style)],
            [Paragraph("Razorpay Payment ID", bold_label), Paragraph(escape(razorpay_payment_id), code_style)],
        ])
    elif has_rzp_order:
        tx_rows.extend([
            [Paragraph("Payment Gateway", bold_label), Paragraph(escape(gateway_display), body_style)],
            [Paragraph("Razorpay Order ID", bold_label), Paragraph(escape(razorpay_order_id), code_style)],
            [Paragraph("Razorpay Payment ID", bold_label), Paragraph("NOT CAPTURED", code_style)],
        ])
    else:
        tx_rows.extend([
            [Paragraph("Settlement Method", bold_label), Paragraph(escape(gateway_display), body_style)],
            [Paragraph("Booking Reference", bold_label), Paragraph(escape(str(confirmation_ref or tx_id)), code_style)],
        ])
    tx_rows.append(
        [Paragraph("Total Amount Settled", bold_label), Paragraph(f"<b>{_fmt_money(amount, currency)}</b>", body_style)],
    )

    story.append(Paragraph("Payment Summary", h2_style))
    t1 = Table(tx_rows, colWidths=[48 * mm, 130 * mm], hAlign="LEFT")
    t1.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t1)

    # 2. Service & Customer Information
    service_rows = [
        [Paragraph("Service Category", bold_label), Paragraph(escape(service_type), body_style)],
        [Paragraph("Item Description", bold_label), Paragraph(escape(service_item), body_style)],
        [Paragraph("Customer Name", bold_label), Paragraph(escape(customer_name), body_style)],
        [Paragraph("Customer Email", bold_label), Paragraph(escape(customer_email), body_style)],
        [Paragraph("Supplier Booking Status", bold_label), Paragraph(f"<b>{escape(booking_display)}</b>", body_style)],
    ]
    story.append(Paragraph("Booking & Traveler Association", h2_style))
    t2 = Table(service_rows, colWidths=[48 * mm, 130 * mm], hAlign="LEFT")
    t2.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f8fafc")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t2)

    # 3. Explicit Legal Notice: Payment vs Supplier Reservation
    story.append(Spacer(1, 4 * mm))
    disclaimer_block = [
        Paragraph("Important Financial Notice & Disclaimer", h2_style),
        Paragraph(disclaimer_text, small_style),
    ]
    story.append(KeepTogether(disclaimer_block))

    def footer(canvas, doc_obj):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(16 * mm, 8 * mm, "TrustTrip MAS — Authorized Payment Record")
        canvas.drawRightString(A4[0] - 16 * mm, 8 * mm, f"Page {doc_obj.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    data = buf.getvalue()
    if not data.startswith(b"%PDF") or len(data) < 500:
        raise ValueError("Payment receipt PDF generation produced an invalid or empty PDF.")
    return data
