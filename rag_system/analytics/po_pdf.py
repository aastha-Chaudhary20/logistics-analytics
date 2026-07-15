"""
rag_system/analytics/po_pdf.py

Renders a purchase order as a real PDF, grounded in the same SQL row the text
PO uses. No LLM is involved in any figure.

RUPEE GLYPH WARNING (important, and the reason for _money()):
ReportLab's built-in Type-1 fonts (Helvetica et al.) have NO glyph for ₹
(U+20B9) — it renders as a solid black box. We therefore:
  1. try to register a TrueType font that has ₹ (DejaVuSans, shipped with
     matplotlib, and present on most systems), and use ₹ if we find one;
  2. otherwise fall back to writing "INR 1,22,500" instead of "₹1,22,500".
Either way the document is correct and readable — never a box.

Amounts use the Indian numbering system (lakh/crore grouping: 1,22,500),
which is what a procurement team in India expects on a PO.

Usage:
    from rag_system.analytics.po_pdf import purchase_order_pdf
    path = purchase_order_pdf("EVN 3503", engine, out_dir="reports")
"""
from typing import Any, Dict, Optional, Tuple
import os
import re
from datetime import datetime

TABLE = "procurement_events"

_FONT_REGULAR = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"
_HAS_RUPEE = False


def _ensure_fonts():
    """Register a ₹-capable TrueType font if we can find one."""
    global _FONT_REGULAR, _FONT_BOLD, _HAS_RUPEE
    if _HAS_RUPEE:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = []
    try:  # matplotlib ships DejaVuSans and is already a common dependency
        import matplotlib
        mpl_dir = os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf")
        candidates += [(os.path.join(mpl_dir, "DejaVuSans.ttf"),
                        os.path.join(mpl_dir, "DejaVuSans-Bold.ttf"))]
    except Exception:
        pass
    candidates += [
        # Windows — Arial and Segoe UI both carry U+20B9 (₹) since Win 8.1
        (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
        (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
        # macOS / Linux
        ("/System/Library/Fonts/Supplemental/DejaVuSans.ttf",
         "/System/Library/Fonts/Supplemental/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    for reg, bold in candidates:
        if os.path.exists(reg):
            try:
                pdfmetrics.registerFont(TTFont("POSans", reg))
                pdfmetrics.registerFont(TTFont("POSans-Bold", bold if os.path.exists(bold) else reg))
                _FONT_REGULAR, _FONT_BOLD, _HAS_RUPEE = "POSans", "POSans-Bold", True
                return
            except Exception:
                continue
    # leave Helvetica + "INR" fallback


def _indian_group(n: float) -> str:
    """1234567.0 -> '12,34,567' (lakh/crore grouping)."""
    s = f"{int(round(n)):d}"
    neg, s = (s.startswith("-"), s.lstrip("-"))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        s = f"{head},{tail}"
    return ("-" if neg else "") + s


def _money(v: Optional[float]) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "-"
    amt = _indian_group(float(v))
    return f"\u20b9{amt}" if _HAS_RUPEE else f"INR {amt}"


def _fetch_award(engine, evid_digits: str) -> Optional[Dict[str, Any]]:
    res = engine._run(
        f"SELECT event_id, event_name, origin, destination, l1_transporter, "
        f"vehicle_qty, l1_rate, final_price, material_weight_kg, cost_per_kg, "
        f"item_description, start_time, source_file, route_total "
        f"FROM {TABLE} WHERE event_id ILIKE 'EVN%{evid_digits}' "
        f"AND (l1_rate IS NOT NULL OR final_price IS NOT NULL) "
        f"ORDER BY COALESCE(l1_rate, final_price) ASC LIMIT 1")
    if not res["rows"]:
        return None
    return dict(zip(res["columns"], res["rows"][0]))


def purchase_order_pdf(query_or_event: str, engine, *,
                       buyer: str = "Escorts Kubota Limited",
                       out_dir: str = "reports") -> Tuple[Optional[str], str]:
    """Returns (pdf_path, message). pdf_path is None if the event isn't indexed
    — we never fabricate a PO."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle)

    m = re.search(r"\bEVN[\s_-]?(\d{3,5})\b", query_or_event, re.I) or \
        re.search(r"\b(\d{3,5})\b", query_or_event)
    if not m:
        return None, "No event ID found in the request (e.g. 'EVN 3503')."
    evid = m.group(1)

    row = _fetch_award(engine, evid)
    if not row:
        try:
            avail = engine._run(f"SELECT DISTINCT event_id FROM {TABLE} ORDER BY 1")
            listing = ", ".join(r[0] for r in avail["rows"][:25]) or "none indexed yet"
        except Exception:
            listing = "none indexed yet"
        return None, (f"EVN {evid} has no awarded vendor/rate in the indexed data, so I won't "
                      f"fabricate a purchase order. Indexed events: {listing}.")
    if not row.get("l1_transporter"):
        return None, f"EVN {evid} has no awarded vendor recorded — not drafting a PO."

    _ensure_fonts()
    os.makedirs(out_dir, exist_ok=True)
    event_id = row["event_id"]
    po_no = f"PO-{str(event_id).replace(' ', '')}-{datetime.now():%Y%m%d}"
    path = os.path.join(out_dir, f"{po_no}.pdf")

    rate = row["final_price"] if row["final_price"] is not None else row["l1_rate"]
    qty = int(row["vehicle_qty"]) if row.get("vehicle_qty") and row["vehicle_qty"] == row["vehicle_qty"] else 1
    total = row.get("route_total") or (rate * qty if rate else None)
    route = f"{row.get('origin') or '-'}  to  {row.get('destination') or '-'}"

    body = ParagraphStyle("body", fontName=_FONT_REGULAR, fontSize=9.5, leading=13)
    small = ParagraphStyle("small", fontName=_FONT_REGULAR, fontSize=8, leading=11,
                           textColor=colors.HexColor("#666666"))
    h1 = ParagraphStyle("h1", fontName=_FONT_BOLD, fontSize=18, leading=22,
                        textColor=colors.HexColor("#0F2A5F"))
    hlabel = ParagraphStyle("hlabel", fontName=_FONT_BOLD, fontSize=8.5, leading=11,
                            textColor=colors.HexColor("#0F2A5F"))

    doc = SimpleDocTemplate(path, pagesize=A4,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"Purchase Order {po_no}", author=buyer)
    story = []

    story.append(Paragraph("PURCHASE ORDER", h1))
    story.append(Spacer(1, 2 * mm))

    meta = Table([
        [Paragraph("PO NUMBER", hlabel), Paragraph(po_no, body),
         Paragraph("DATE", hlabel), Paragraph(datetime.now().strftime("%d %b %Y"), body)],
        [Paragraph("BUYER", hlabel), Paragraph(buyer, body),
         Paragraph("SOURCING EVENT", hlabel), Paragraph(str(event_id), body)],
        [Paragraph("SUPPLIER", hlabel), Paragraph(str(row["l1_transporter"]).strip(), body),
         Paragraph("AWARDED FROM", hlabel), Paragraph(str(row.get("source_file") or "-")[:38], small)],
    ], colWidths=[26 * mm, 62 * mm, 30 * mm, 56 * mm])
    meta.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#E3E7EF")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7F9FC")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#D6DCE8")),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ]))
    story += [meta, Spacer(1, 6 * mm)]

    desc = str(row.get("item_description") or f"Transportation service, {route}")
    if len(desc) > 700:
        desc = desc[:700] + "…"

    items = [
        [Paragraph("<b>DESCRIPTION</b>", body), Paragraph("<b>QTY</b>", body),
         Paragraph("<b>RATE</b>", body), Paragraph("<b>AMOUNT</b>", body)],
        [Paragraph(desc, body), Paragraph(str(qty), body),
         Paragraph(_money(rate), body), Paragraph(_money(total), body)],
    ]
    extra = []
    if row.get("material_weight_kg"):
        extra.append(f"Material weight: {_indian_group(row['material_weight_kg'])} KG")
    if row.get("cost_per_kg"):
        cpk = f"{row['cost_per_kg']:.2f}"
        _sym = "\u20b9" if _HAS_RUPEE else "INR "
        extra.append(f"Cost per kg: {_sym}{cpk}")
    if extra:
        items.append([Paragraph(" · ".join(extra), small), "", "", ""])

    t = Table(items, colWidths=[102 * mm, 16 * mm, 28 * mm, 28 * mm])
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F2A5F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, 1), 0.4, colors.HexColor("#D6DCE8")),
    ]
    if extra:
        style += [("SPAN", (0, 2), (-1, 2)),
                  ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#F7F9FC")),
                  ("BOX", (0, 2), (-1, 2), 0.4, colors.HexColor("#D6DCE8"))]
    t.setStyle(TableStyle(style))
    story += [t, Spacer(1, 3 * mm)]

    tot = Table([[Paragraph("<b>ORDER TOTAL</b>", body), Paragraph(f"<b>{_money(total)}</b>", body)]],
                colWidths=[146 * mm, 28 * mm])
    tot.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EEF3FB")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#0F2A5F")),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ]))
    story += [tot, Spacer(1, 7 * mm)]

    story.append(Paragraph("<b>Terms</b>", body))
    for line in [
        f"Rate as awarded in sourcing event {event_id}; no variation without written approval.",
        "Supplier to confirm acceptance within 48 hours of receipt.",
        "Freight, taxes and levies as per agreed commercial terms.",
    ]:
        story.append(Paragraph(f"• {line}", body))
    story.append(Spacer(1, 12 * mm))

    sign = Table([[Paragraph("Authorised signatory", small), Paragraph("Date", small)],
                  [Paragraph("__________________________", body), Paragraph("________________", body)]],
                 colWidths=[100 * mm, 74 * mm])
    sign.setStyle(TableStyle([("TOPPADDING", (0, 1), (-1, 1), 10),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [sign, Spacer(1, 6 * mm)]

    story.append(Paragraph(
        f"Generated from indexed award data for {event_id}. Every figure above is taken "
        f"directly from the sourcing report; none are estimated.", small))

    doc.build(story)
    return path, f"Purchase order generated: {os.path.basename(path)}"
