"""
rag_system/analytics/okf_export.py

Open Knowledge Format export — turns the procurement_events table into portable,
standard formats that other tools can read, instead of locking the knowledge
inside this app's DuckDB/LanceDB files.

Three interoperable outputs, all derived from the SAME computed rows:

  1. JSON-LD  (schema.org vocabulary)  -> for web / graph tools, Google-friendly
  2. N-Triples (RDF)                   -> load into any triple store (RDFLib,
                                          Apache Jena, GraphDB, Neo4j n10s)
  3. CSV                               -> the flat table, for Excel / pandas / BI

The graph models each sourcing event as a node linked to its winning vendor,
origin, and destination — so an external tool can traverse
"which vendors served which lanes" without touching this codebase.

Wire into api_server.py:
    from rag_system.analytics.okf_export import export_okf
    GET /knowledge/export?format=jsonld|ntriples|csv
        -> writes reports/knowledge.<ext> and returns the path

CLI:
    python -m rag_system.analytics.okf_export            # writes all three
"""
from typing import Any, Dict, List, Optional
import json
import os
import re

STRUCTURED_DB = os.environ.get("STRUCTURED_DB", "./index_store/structured.duckdb")
TABLE = "procurement_events"
BASE = "urn:stellarkeep:"          # our namespace for entity IRIs


def _rows(db_path: str = STRUCTURED_DB) -> List[Dict[str, Any]]:
    import duckdb
    if not os.path.exists(db_path):
        return []
    try:
        con = duckdb.connect(db_path, read_only=True)
    except Exception:
        con = duckdb.connect(db_path)
    try:
        if TABLE not in [r[0] for r in con.execute("SHOW TABLES").fetchall()]:
            return []
        cur = con.execute(f"""
            SELECT event_id, event_name, origin, destination, l1_transporter,
                   vehicle_qty, l1_rate, final_price, material_weight_kg,
                   cost_per_kg, participants, start_time, source_file
            FROM {TABLE}
            WHERE l1_rate IS NOT NULL OR final_price IS NOT NULL""")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


def _iri(kind: str, value: Any) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "unknown").strip().lower()).strip("-")
    return f"{BASE}{kind}:{slug or 'unknown'}"


def _num(v):
    return None if v is None or (isinstance(v, float) and v != v) else v


# --------------------------------------------------------------------------- #
# JSON-LD (schema.org)
# --------------------------------------------------------------------------- #
def to_jsonld(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    graph = []
    vendors, places = {}, {}
    for r in rows:
        ev_iri = _iri("event", r["event_id"])
        vendor = (r.get("l1_transporter") or "").strip()
        origin = (r.get("origin") or "").strip()
        dest = (r.get("destination") or "").strip()

        node = {
            "@id": ev_iri,
            "@type": "BuyAction",           # schema.org: a procurement action
            "identifier": r.get("event_id"),
            "name": r.get("event_name"),
            "price": _num(r.get("final_price")) or _num(r.get("l1_rate")),
            "priceCurrency": "INR",
        }
        if vendor:
            v_iri = _iri("vendor", vendor)
            vendors[v_iri] = {"@id": v_iri, "@type": "Organization", "name": vendor}
            node["seller"] = {"@id": v_iri}
        if origin:
            o_iri = _iri("place", origin)
            places[o_iri] = {"@id": o_iri, "@type": "Place", "name": origin}
            node["fromLocation"] = {"@id": o_iri}
        if dest:
            d_iri = _iri("place", dest)
            places[d_iri] = {"@id": d_iri, "@type": "Place", "name": dest}
            node["toLocation"] = {"@id": d_iri}
        extra = {}
        if _num(r.get("material_weight_kg")) is not None:
            extra["weightKg"] = _num(r["material_weight_kg"])
        if _num(r.get("cost_per_kg")) is not None:
            extra["costPerKg"] = _num(r["cost_per_kg"])
        if _num(r.get("participants")) is not None:
            extra["bidderCount"] = int(_num(r["participants"]))
        if extra:
            node["additionalProperty"] = extra
        node["source"] = r.get("source_file")
        graph.append(node)

    return {
        "@context": {
            "@vocab": "https://schema.org/",
            "weightKg": "urn:stellarkeep:weightKg",
            "costPerKg": "urn:stellarkeep:costPerKg",
            "bidderCount": "urn:stellarkeep:bidderCount",
            "source": "urn:stellarkeep:sourceFile",
        },
        "@graph": graph + list(vendors.values()) + list(places.values()),
    }


# --------------------------------------------------------------------------- #
# N-Triples (RDF)
# --------------------------------------------------------------------------- #
def to_ntriples(rows: List[Dict[str, Any]]) -> str:
    S = "https://schema.org/"
    lines: List[str] = []

    def trip(s, p, o_iri=None, lit=None, dtype=None):
        obj = f"<{o_iri}>" if o_iri else (
            f'"{str(lit)}"' + (f"^^<{dtype}>" if dtype else "")
        )
        lines.append(f"<{s}> <{p}> {obj} .")

    XSD = "http://www.w3.org/2001/XMLSchema#"
    for r in rows:
        ev = _iri("event", r["event_id"])
        trip(ev, "http://www.w3.org/1999/02/22-rdf-syntax-ns#type", o_iri=f"{S}BuyAction")
        if r.get("event_id"):
            trip(ev, f"{S}identifier", lit=r["event_id"])
        price = _num(r.get("final_price")) or _num(r.get("l1_rate"))
        if price is not None:
            trip(ev, f"{S}price", lit=price, dtype=f"{XSD}decimal")
            trip(ev, f"{S}priceCurrency", lit="INR")
        if (r.get("l1_transporter") or "").strip():
            v = _iri("vendor", r["l1_transporter"])
            trip(ev, f"{S}seller", o_iri=v)
            trip(v, "http://www.w3.org/1999/02/22-rdf-syntax-ns#type", o_iri=f"{S}Organization")
            trip(v, f"{S}name", lit=r["l1_transporter"].strip())
        if (r.get("origin") or "").strip():
            o = _iri("place", r["origin"])
            trip(ev, f"{S}fromLocation", o_iri=o)
            trip(o, f"{S}name", lit=r["origin"].strip())
        if (r.get("destination") or "").strip():
            d = _iri("place", r["destination"])
            trip(ev, f"{S}toLocation", o_iri=d)
            trip(d, f"{S}name", lit=r["destination"].strip())
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #
def to_csv(rows: List[Dict[str, Any]]) -> str:
    import csv
    import io
    if not rows:
        return ""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def export_okf(fmt: str = "jsonld", out_dir: str = "reports",
               db_path: str = STRUCTURED_DB) -> Dict[str, Any]:
    rows = _rows(db_path)
    if not rows:
        return {"error": "No structured rows to export — index some files first."}
    os.makedirs(out_dir, exist_ok=True)
    fmt = fmt.lower()
    if fmt in ("jsonld", "json-ld", "json"):
        path = os.path.join(out_dir, "knowledge.jsonld")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(to_jsonld(rows), f, ensure_ascii=False, indent=2, default=str)
    elif fmt in ("ntriples", "nt", "rdf"):
        path = os.path.join(out_dir, "knowledge.nt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(to_ntriples(rows))
    elif fmt == "csv":
        path = os.path.join(out_dir, "knowledge.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write(to_csv(rows))
    else:
        return {"error": f"Unknown format '{fmt}'. Use jsonld, ntriples, or csv."}
    return {"format": fmt, "path": path, "events": len({r['event_id'] for r in rows}),
            "rows": len(rows)}


if __name__ == "__main__":
    for f in ("jsonld", "ntriples", "csv"):
        print(export_okf(f))
