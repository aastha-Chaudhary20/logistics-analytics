"""
rag_system/analytics/normalizer.py

Maps every parsed report (any layout) into ONE canonical DuckDB table:

    procurement_events(
        event_id, event_name, origin, destination,
        start_time, end_time, participants,
        item_description, vehicle_qty, l1_rate, l1_transporter,
        final_price, material_weight_kg, cost_per_kg,
        source_file, ingested_at
    )

This is what makes cross-file analytics possible: "total spend by vendor",
"cheapest cost/kg lane", "price history for FBD routes" all become plain SQL
over this table. No LLM, no embeddings — ingest is pure parsing, so a few
thousand files normalize in minutes on CPU.

Idempotent per file: re-ingesting a file replaces its own rows only.
"""
from typing import Any, Dict, List, Optional
import os
import re
from datetime import datetime, timezone

TABLE = "procurement_events"

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    event_id VARCHAR,
    event_name VARCHAR,
    origin VARCHAR,
    destination VARCHAR,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    participants INTEGER,
    item_description VARCHAR,
    vehicle_qty DOUBLE,
    l1_rate DOUBLE,
    l1_transporter VARCHAR,
    final_price DOUBLE,
    material_weight_kg DOUBLE,
    cost_per_kg DOUBLE,
    source_file VARCHAR,
    ingested_at TIMESTAMP
)
"""

# --------------------------------------------------------------------------- #
# field extraction helpers
# --------------------------------------------------------------------------- #
def _meta_value(meta_lines: List[str], *prefixes) -> str:
    for l in meta_lines or []:
        low = l.lower()
        for p in prefixes:
            if low.startswith(p):
                return l.split(":", 1)[-1].strip()
    return ""


def _parse_route(event_name: str):
    """'... from X to Y[, extra]' -> (X, Y). Tolerant of commas/pincode tails."""
    m = re.search(r"\bfrom\s+(.+?)\s+to\s+(.+)$", event_name, re.I)
    if not m:
        return "", ""
    origin = m.group(1).strip(" .,-")
    dest = m.group(2).strip(" .,-")
    dest = re.sub(r"[-\s]*\d{6}\s*$", "", dest).strip(" .,-")  # drop pincode tail
    return origin[:120], dest[:120]


def _parse_timeline(s: str):
    """' 2026-02-14 11:00 to 2026-02-16 16:45' -> (start, end) or (None, None)."""
    m = re.findall(r"(\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2})", s or "")
    def cv(x):
        try:
            return datetime.strptime(x.replace("T", " "), "%Y-%m-%d %H:%M")
        except Exception:
            return None
    return (cv(m[0]) if m else None, cv(m[1]) if len(m) > 1 else None)


def _num(v) -> Optional[float]:
    if v is None:
        return None
    s = re.sub(r"[,₹$%\s]", "", str(v))
    try:
        f = float(s)
        return f
    except ValueError:
        return None


def _weight_kg(text: str) -> Optional[float]:
    """Pull 'Material Weight: 21600KG' / '2,20 kg' style figures from prose."""
    m = re.search(r"(?:material\s+weight|weight)\s*[:\-]?\s*([\d,\.]+)\s*(kg|kgs|mt|ton)", str(text), re.I)
    if not m:
        return None
    val = _num(m.group(1))
    if val is None:
        return None
    unit = m.group(2).lower()
    return val * 1000 if unit in ("mt", "ton") else val


def _pick_col(cols: List[str], *needles, forbid=()) -> Optional[str]:
    """Fuzzy column match: first column whose name contains all needles."""
    for c in cols:
        low = str(c).lower()
        if all(n in low for n in needles) and not any(f in low for f in forbid):
            return c
    return None


# --------------------------------------------------------------------------- #
# main entry — called by FileRouter after a frame is parsed
# --------------------------------------------------------------------------- #
def normalize_frame(df, meta_lines: List[str], source: str) -> List[Dict[str, Any]]:
    cols = [str(c) for c in df.columns]
    event_id = _meta_value(meta_lines, "event id")
    event_name = _meta_value(meta_lines, "event name")
    participants = _num(_meta_value(meta_lines, "no. of participants", "participants"))
    start, end = _parse_timeline(_meta_value(meta_lines, "event timeline", "timeline"))
    origin, dest = _parse_route(event_name)
    # fall back to filename for event id (files are named 'EVN 3456 Report ...')
    if not event_id:
        m = re.search(r"\bEVN[\s_-]?(\d{3,5})\b", source, re.I)
        event_id = f"EVN {m.group(1)}" if m else ""

    c_desc = _pick_col(cols, "desc") or _pick_col(cols, "item")
    c_qty = _pick_col(cols, "qty") or _pick_col(cols, "quantity")
    c_rate = _pick_col(cols, "l1", "rate") or _pick_col(cols, "rate", forbid=("rank",))
    c_vendor = _pick_col(cols, "transporter") or _pick_col(cols, "vendor") or _pick_col(cols, "supplier")
    c_final = _pick_col(cols, "final", "price") or _pick_col(cols, "final")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows: List[Dict[str, Any]] = []
    for rec in df.to_dict("records"):
        desc = str(rec.get(c_desc, "") or "") if c_desc else ""
        l1 = _num(rec.get(c_rate)) if c_rate else None
        final = _num(rec.get(c_final)) if c_final else None
        wt = _weight_kg(desc)
        price_for_kg = final if final is not None else l1
        rows.append({
            "event_id": event_id, "event_name": event_name,
            "origin": origin, "destination": dest,
            "start_time": start, "end_time": end,
            "participants": int(participants) if participants else None,
            "item_description": desc[:2000] or None,
            "vehicle_qty": _num(rec.get(c_qty)) if c_qty else None,
            "l1_rate": l1,
            "l1_transporter": (str(rec.get(c_vendor)).strip() or None) if c_vendor else None,
            "final_price": final,
            "material_weight_kg": wt,
            "cost_per_kg": round(price_for_kg / wt, 4) if (price_for_kg and wt) else None,
            "source_file": source,
            "ingested_at": now,
        })
    # keep only rows that carry a real awarded value: a rate/price AND either a
    # vendor or a weight. This drops 'Total Cost' summary rows and trailing notes
    # that would otherwise appear as empty (nan) rows in comparisons.
    def _keep(r):
        has_money = r["l1_rate"] is not None or r["final_price"] is not None
        has_ctx = bool(r["l1_transporter"]) or r["material_weight_kg"] is not None
        desc = (r["item_description"] or "").lower()
        if desc.startswith(("total cost", "rates are", "note", "grand total")):
            return False
        return has_money and has_ctx
    rows = [r for r in rows if _keep(r)]
    return rows


def upsert_rows(con, rows: List[Dict[str, Any]], source: str):
    """Idempotent per source file: replace this file's rows, keep everything else."""
    if not rows:
        return 0
    con.execute(DDL)
    con.execute(f"DELETE FROM {TABLE} WHERE source_file = ?", [source])
    keys = list(rows[0].keys())
    placeholders = ", ".join(["?"] * len(keys))
    con.executemany(
        f'INSERT INTO {TABLE} ({", ".join(keys)}) VALUES ({placeholders})',
        [[r[k] for k in keys] for r in rows],
    )
    return len(rows)
