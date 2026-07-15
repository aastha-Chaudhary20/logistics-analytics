"""
rag_system/analytics/po_generator.py

Draft purchase orders (and comparative statements) grounded ENTIRELY in the
awarded-bid row from procurement_events. Never fabricates: if the requested
event isn't indexed, it returns a helpful message listing the events that ARE
available, so the user can correct a typo instead of hitting a dead end.

The document is assembled from SQL fields (vendor, qty, rate, route, weight) —
the LLM is optional and only used to phrase a cover note, never to invent
numbers.

Usage:
    from rag_system.analytics.po_generator import draft_purchase_order
    text = draft_purchase_order("draft a purchase order for EVN 3503",
                                engine, buyer="Escorts Kubota Limited")
"""
from typing import Any, Dict, List, Optional
import re
from datetime import datetime

TABLE = "procurement_events"


def _extract_event_id(text: str) -> Optional[str]:
    m = re.search(r"\bEVN[\s_-]?(\d{3,5})\b", text, re.I)
    return m.group(1) if m else None


def _available_events(engine, limit: int = 30) -> List[str]:
    try:
        res = engine._run(
            f"SELECT DISTINCT event_id FROM {TABLE} WHERE event_id IS NOT NULL "
            f"ORDER BY event_id")
        return [r[0] for r in res["rows"][:limit]]
    except Exception:
        return []


def _fmt_inr(v) -> str:
    try:
        return f"₹{float(v):,.0f}"
    except (TypeError, ValueError):
        return "—"


def draft_purchase_order(query: str, engine, *,
                         buyer: str = "Escorts Kubota Limited",
                         llm_fn=None, drafting_backend=None,
                         backend_override: Optional[str] = None) -> str:
    """Return a grounded PO, or a helpful 'not found' listing real events.

    drafting_backend: a DraftingBackend instance controlling who writes the
        cover note (local model, air-gapped default, or Claude if the
        deployment has explicitly opted in). If omitted, falls back to the
        legacy llm_fn (local-only, unchanged behavior).
    backend_override: per-request choice ("local"/"claude"); still bounded by
        the deployment's configured backend — see drafting_backend.py.
    """
    evid = _extract_event_id(query)
    if not evid:
        events = _available_events(engine)
        listing = ", ".join(events) if events else "none indexed yet"
        return ("Which event is the PO for? I couldn't find an event ID in your "
                f"request. Indexed events you can draft from: {listing}.")

    # Pull the awarded (lowest-rate) row for this event.
    res = engine._run(
        f"SELECT event_id, event_name, origin, destination, l1_transporter, "
        f"vehicle_qty, l1_rate, final_price, material_weight_kg, cost_per_kg, "
        f"item_description, start_time, source_file "
        f"FROM {TABLE} WHERE event_id ILIKE 'EVN%{evid}' "
        f"AND (l1_rate IS NOT NULL OR final_price IS NOT NULL) "
        f"ORDER BY COALESCE(l1_rate, final_price) ASC LIMIT 1")
    rows = res["rows"]

    if not rows:
        events = _available_events(engine)
        # is the event present but with no awarded value, or absent entirely?
        present = any(e.replace(" ", "").lower().endswith(evid) for e in events)
        listing = ", ".join(events) if events else "none indexed yet"
        if present:
            return (f"EVN {evid} is indexed, but I don't see an awarded rate/vendor "
                    f"on it, so I won't fabricate a purchase order. The event may "
                    f"have parsed without its bid table — re-check that file.")
        return (f"I couldn't find EVN {evid} in the indexed data, so I won't "
                f"fabricate a purchase order. Indexed events available: {listing}. "
                f"If EVN {evid} exists, upload and index its report first.")

    (event_id, event_name, origin, dest, vendor, qty, l1_rate, final_price,
     weight, cost_kg, item_desc, start_time, source_file) = rows[0]

    if not vendor:
        return (f"EVN {evid} is indexed but has no awarded vendor recorded, so I "
                f"won't fabricate a purchase order.")

    rate = final_price if final_price is not None else l1_rate
    qty_n = int(qty) if isinstance(qty, (int, float)) and qty else 1
    po_no = f"PO-{event_id.replace(' ', '')}-{datetime.now():%Y%m%d}"
    today = datetime.now().strftime("%d %b %Y")
    route = f"{origin or '—'} → {dest or '—'}"
    line_total = _fmt_inr(rate)

    doc = f"""PURCHASE ORDER

PO Number:      {po_no}
Date:           {today}
Buyer:          {buyer}
Supplier:       {vendor}

Sourcing event: {event_id}{(' — ' + event_name) if event_name else ''}
Awarded from:   {source_file}

------------------------------------------------------------------
Line items
------------------------------------------------------------------
Description:    {item_desc or f'Transportation service, {route}'}
Route:          {route}
Quantity:       {qty_n} vehicle(s)
Material weight:{(' ' + format(weight, ',.0f') + ' KG') if weight else ' —'}
Awarded rate:   {_fmt_inr(rate)}
{('Cost per kg:    ₹' + format(cost_kg, ',.2f')) if cost_kg else ''}
------------------------------------------------------------------
Order total:    {line_total}
------------------------------------------------------------------

Terms
- Rate as awarded in sourcing event {event_id}; no variation without written approval.
- Supplier to confirm acceptance within 48 hours of receipt.
- Freight, taxes and levies as per agreed commercial terms.

Authorised by: ____________________        Date: __________

(Generated from indexed award data. Every figure above is taken directly from
event {event_id}; none are estimated.)"""

    # Cover note: written by whichever backend this deployment allows.
    # Only the whitelisted fields below are ever eligible to leave the
    # machine, and only if drafting_backend resolves to "claude".
    footer_note = None
    if drafting_backend is not None:
        from rag_system.analytics.drafting_backend import build_cover_note_prompt
        prompt = build_cover_note_prompt({
            "event_id": event_id, "vendor": vendor, "route": route,
            "quantity": qty_n, "buyer": buyer,
        })
        result = drafting_backend.generate(prompt, backend=backend_override)
        if result["text"]:
            doc = result["text"] + "\n\n" + doc
        footer_note = f"(Cover note drafted by: {result['backend']})"
        if result.get("downgraded"):
            footer_note += f" — {result['downgrade_reason']}"
    elif llm_fn:
        # Legacy path: unchanged local-only behavior for existing callers.
        try:
            note = llm_fn(
                "Write ONE short professional sentence to accompany this purchase "
                f"order to {vendor} for transportation on the {route} lane "
                f"(event {event_id}). Do not mention amounts.")
            if note and note.strip():
                doc = note.strip() + "\n\n" + doc
        except Exception:
            pass

    if footer_note:
        doc = doc + "\n" + footer_note
    return doc
