"""
rag_system/analytics/index_inspector.py

Answers "what is actually IN this index?" so you never have to guess why a
question failed after switching indexes.

Two storage layers hold your data, and they're scoped differently:
  • RAG / documents  -> per-index LanceDB table (each index isolated)
  • analytics rows   -> ONE shared DuckDB 'procurement_events' (all indexes pooled)

This inspector reports both, per index and overall, and flags the common
failure: files that were indexed for RAG but never landed in the analytics
table (or vice-versa).

Wire into api_server.py:
    from rag_system.analytics.index_inspector import inspect_index, inspect_all
    GET /index/inspect?table=<vector_table_name>   -> inspect_index(...)
    GET /index/inspect                              -> inspect_all(...)
"""
from typing import Any, Dict, List, Optional
import os

STRUCTURED_DB = os.environ.get("STRUCTURED_DB", "./index_store/structured.duckdb")
LANCEDB_URI = os.environ.get("LANCEDB_URI", "./lancedb")
TABLE = "procurement_events"


def _analytics_summary(db_path: str = STRUCTURED_DB) -> Dict[str, Any]:
    """What the shared analytics table holds (spans ALL indexes)."""
    try:
        import duckdb
    except ImportError:
        return {"available": False, "reason": "duckdb not installed"}
    if not os.path.exists(db_path):
        return {"available": False, "reason": f"no analytics db at {db_path}"}
    try:
        con = duckdb.connect(db_path, read_only=True)
    except Exception:
        con = duckdb.connect(db_path)
    try:
        tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
        if TABLE not in tables:
            return {"available": True, "rows": 0, "events": 0, "files": [],
                    "note": "analytics table not created yet — no structured rows indexed"}
        rows = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
        events = con.execute(f"SELECT COUNT(DISTINCT event_id) FROM {TABLE}").fetchone()[0]
        files = [r[0] for r in con.execute(
            f"SELECT DISTINCT source_file FROM {TABLE} ORDER BY 1").fetchall()]
        evids = [r[0] for r in con.execute(
            f"SELECT DISTINCT event_id FROM {TABLE} WHERE event_id IS NOT NULL ORDER BY 1").fetchall()]
        return {"available": True, "rows": rows, "events": events,
                "files": files, "event_ids": evids,
                "note": "analytics is SHARED across all indexes"}
    finally:
        con.close()


def _lancedb_summary(table_name: Optional[str], uri: str = LANCEDB_URI) -> Dict[str, Any]:
    """What a specific index's RAG table holds (this index only)."""
    if not table_name:
        return {"available": False, "reason": "no vector table name for this index"}
    try:
        import lancedb
    except ImportError:
        return {"available": False, "reason": "lancedb not installed"}
    try:
        db = lancedb.connect(uri)
        names = db.table_names()
        if table_name not in names:
            return {"available": True, "chunks": 0, "sources": [],
                    "note": f"table '{table_name}' has no rows yet — nothing indexed for RAG in this index",
                    "all_tables": names}
        tbl = db.open_table(table_name)
        df = tbl.to_pandas()
        chunks = len(df)
        src_col = next((c for c in ("source", "document_id", "source_file", "metadata")
                        if c in df.columns), None)
        sources: List[str] = []
        if src_col and src_col != "metadata":
            sources = sorted({str(v) for v in df[src_col].dropna().unique()})[:100]
        return {"available": True, "chunks": chunks, "sources": sources,
                "note": "RAG documents are PER-INDEX (isolated to this index)"}
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


def inspect_index(vector_table_name: Optional[str]) -> Dict[str, Any]:
    """Full picture for one index: its RAG table + the shared analytics view."""
    rag = _lancedb_summary(vector_table_name)
    analytics = _analytics_summary()
    # Flag the mismatch that causes "it can't answer" confusion.
    warnings = []
    if rag.get("chunks", 0) == 0:
        warnings.append("This index has NO document chunks — RAG/prose questions "
                        "will return nothing. Re-index files into it.")
    if analytics.get("rows", 0) == 0:
        warnings.append("The analytics table is empty — number questions (spend, "
                        "L1 rate) will fail everywhere until structured files are indexed.")
    return {"index_table": vector_table_name, "rag": rag,
            "analytics_shared": analytics, "warnings": warnings}


def inspect_all(db) -> Dict[str, Any]:
    """Every index the app knows about, plus the shared analytics summary.
    `db` is the ChatDatabase instance (to list indexes)."""
    out_indexes = []
    try:
        for idx in db.list_indexes():
            vt = idx.get("vector_table_name")
            rag = _lancedb_summary(vt)
            out_indexes.append({
                "id": idx.get("id"), "name": idx.get("name"),
                "vector_table": vt, "rag_chunks": rag.get("chunks", 0),
                "rag_sources": rag.get("sources", [])[:20],
            })
    except Exception as e:
        out_indexes = [{"error": f"could not list indexes: {e}"}]
    return {"indexes": out_indexes, "analytics_shared": _analytics_summary()}
