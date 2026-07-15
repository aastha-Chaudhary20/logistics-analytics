"""
rag_system/utils/query_log.py

Logs every chat query with the lane it took, how it was answered, and how
long it took — the raw material for the "add an intent a week" loop that
makes the template set grow to cover what buyers actually ask.

One JSONL line per query in logs/query_log.jsonl:
    {"ts": "...", "session": "abc123", "query": "...", "lane": "analytics",
     "mode": "intent" | "llm_sql" | "report" | "document" | "rag",
     "answered": true, "latency_ms": 412, "note": null}

'answered' is a heuristic: false when the reply is an analytics error,
"unsupported", "no matching records", or a RAG "could not find" style answer.
Those are exactly the queries to review weekly:

    python -m rag_system.utils.query_log            # summary report
    python -m rag_system.utils.query_log --unanswered   # just the misses
"""
from typing import Any, Dict, Optional
import json
import os
import threading
from datetime import datetime, timezone

LOG_PATH = os.getenv("QUERY_LOG_PATH", os.path.join("logs", "query_log.jsonl"))
_lock = threading.Lock()

_MISS_MARKERS = (
    "couldn't run", "could not run", "unsupported", "no matching records",
    "does not contain", "doesn't contain", "could not find", "couldn't find",
    "no record of", "not available in the provided",
)


def _looks_unanswered(answer: Optional[str]) -> bool:
    if not answer:
        return True
    low = answer.lower()
    return any(m in low for m in _MISS_MARKERS)


def log_query(query: str, *, lane: str, mode: Optional[str] = None,
              session_id: Optional[str] = None, answer: Optional[str] = None,
              latency_ms: Optional[float] = None, note: Optional[str] = None):
    """Append one record. Never raises — logging must not break answering."""
    try:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "session": (session_id or "")[:12] or None,
            "query": (query or "")[:500],
            "lane": lane,
            "mode": mode,
            "answered": not _looks_unanswered(answer),
            "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
            "note": note,
        }
        os.makedirs(os.path.dirname(LOG_PATH) or ".", exist_ok=True)
        line = json.dumps(rec, ensure_ascii=False)
        with _lock:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass  # never let telemetry break the request


# --------------------------------------------------------------------------- #
# review report
# --------------------------------------------------------------------------- #
def load_records():
    if not os.path.exists(LOG_PATH):
        return []
    out = []
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def report(unanswered_only: bool = False) -> str:
    recs = load_records()
    if not recs:
        return f"No queries logged yet ({LOG_PATH})."
    lanes: Dict[str, Dict[str, Any]] = {}
    for r in recs:
        d = lanes.setdefault(r.get("lane") or "?", {"n": 0, "miss": 0, "lat": []})
        d["n"] += 1
        if not r.get("answered"):
            d["miss"] += 1
        if r.get("latency_ms") is not None:
            d["lat"].append(r["latency_ms"])
    lines = [f"Query log report — {len(recs)} queries ({LOG_PATH})", ""]
    lines.append(f"{'lane':<11}{'queries':>8}{'unanswered':>12}{'avg ms':>9}")
    for lane, d in sorted(lanes.items(), key=lambda kv: -kv[1]["n"]):
        avg = sum(d["lat"]) / len(d["lat"]) if d["lat"] else 0
        lines.append(f"{lane:<11}{d['n']:>8}{d['miss']:>12}{avg:>9.0f}")
    misses = [r for r in recs if not r.get("answered")]
    if misses:
        lines += ["", f"Unanswered / missed queries ({len(misses)}) — candidates for new intents:"]
        seen = set()
        for r in misses:
            q = r["query"]
            if q in seen:
                continue
            seen.add(q)
            lines.append(f"  [{r.get('lane')}/{r.get('mode')}] {q}")
    if unanswered_only:
        return "\n".join(lines[lines.index(""):]) if misses else "No unanswered queries. 🎉"
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    print(report(unanswered_only="--unanswered" in sys.argv))
