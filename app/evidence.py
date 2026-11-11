"""Evidence Pack: one PDF with all proof for a sale."""
from __future__ import annotations

import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def build_pdf(sale: dict, checklist: list[dict], messages: list[dict]) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                            title=f"Evidence pack - order {sale.get('order_id')}")
    st = getSampleStyleSheet()
    esc = lambda s: (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;")
    story = [
        Paragraph("Evidence Pack", st["Title"]),
        Paragraph(f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by Hold Guard. "
                  "All facts below come from PayPal records and the seller's saved messages.", st["Normal"]),
        Spacer(1, 12),
        Paragraph("Order", st["Heading2"]),
    ]
    rows = [
        ["Item", esc(sale.get("item_name"))],
        ["Type", "Service / digital" if sale.get("item_type") == "service" else "Physical item"],
        ["Amount", f"${sale.get('amount', 0):,.2f} {sale.get('currency', 'USD')}"],
        ["Sale date", esc((sale.get("created_at") or "")[:19].replace("T", " "))],
        ["PayPal order ID", esc(sale.get("order_id"))],
        ["PayPal capture ID", esc(sale.get("capture_id"))],
        ["Buyer", esc(sale.get("buyer_email") or "-")],
    ]
    story.append(_table(rows))
    story += [Spacer(1, 12), Paragraph("Fulfillment", st["Heading2"])]
    if sale.get("item_type") == "service":
        ful = [["Completed", esc(sale.get("completed_at") or "Not yet marked completed")]]
    else:
        ful = [
            ["Carrier", esc(sale.get("carrier") or "-")],
            ["Tracking number", esc(sale.get("tracking_number") or "-")],
            ["Tracking sent to PayPal", esc(sale.get("tracking_sent_at") or "No")],
            ["Delivery confirmed", "Yes" if sale.get("delivered") else "Not yet"],
        ]
    story.append(_table(ful))
    story += [Spacer(1, 12), Paragraph("Checklist", st["Heading2"])]
    story.append(_table([["Done" if c["done"] else "Missing", esc(c["label"])] for c in checklist]))
    story += [Spacer(1, 12), Paragraph("Messages with the buyer", st["Heading2"])]
    if messages:
        for m in messages:
            story.append(Paragraph(f"<b>{esc(m['author'])}</b> ({esc(m['created_at'][:16])}): {esc(m['text'])}",
                                   st["Normal"]))
            story.append(Spacer(1, 4))
    else:
        story.append(Paragraph("No messages saved.", st["Normal"]))
    doc.build(story)
    return buf.getvalue()


def _table(rows):
    t = Table(rows, colWidths=[1.9 * inch, 4.8 * inch])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9),
        ("FONT", (1, 0), (1, -1), "Helvetica", 9),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c8c8c8")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f2f4f7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t
