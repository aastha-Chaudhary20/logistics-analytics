"""
rag_system/utils/index_progress.py

Tracks indexing progress per session so the UI can show a real progress bar
instead of a spinner that hangs for minutes.

The /index endpoint blocks until indexing completes, so the browser can't
learn anything mid-run from that request. Instead the pipeline pushes stage
updates here, and the frontend polls GET /index/progress?session_id=... on a
timer.

Stages (weighted so the bar moves smoothly rather than jumping):
    parsing    0-40%   reading/parsing files      (fast)
    enriching  40-55%  optional LLM enrichment    (skipped when OFF)
    embedding  55-90%  the slow part on CPU
    storing    90-100% vector + FTS index write

Usage from the pipeline:
    from rag_system.utils.index_progress import progress
    progress.start(session_id, total_files=len(file_paths))
    progress.file_done(session_id, filename)          # during parsing
    progress.stage(session_id, "embedding", done=32, total=210)
    progress.finish(session_id)                       # or .fail(session_id, msg)
"""
from typing import Any, Dict, Optional
import threading
import time

# Stage -> (start_pct, end_pct)
_WEIGHTS = {
    "queued":    (0, 0),
    "parsing":   (0, 40),
    "enriching": (40, 55),
    "embedding": (55, 90),
    "storing":   (90, 100),
    "done":      (100, 100),
    "error":     (0, 0),
}


class _ProgressRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._state: Dict[str, Dict[str, Any]] = {}

    def _key(self, session_id: Optional[str]) -> str:
        return session_id or "_global"

    def start(self, session_id, total_files: int):
        with self._lock:
            self._state[self._key(session_id)] = {
                "stage": "parsing", "percent": 0,
                "total_files": total_files, "files_done": 0,
                "current_file": None, "message": f"Reading {total_files} file(s)…",
                "started_at": time.time(), "eta_seconds": None,
                "done": False, "error": None,
            }

    def _set_percent(self, st: Dict[str, Any], stage: str, frac: float):
        lo, hi = _WEIGHTS.get(stage, (0, 100))
        st["stage"] = stage
        st["percent"] = int(lo + (hi - lo) * max(0.0, min(1.0, frac)))
        # simple ETA from elapsed vs completed fraction
        elapsed = time.time() - st.get("started_at", time.time())
        pct = st["percent"]
        if pct > 3 and pct < 100:
            st["eta_seconds"] = int(elapsed * (100 - pct) / pct)

    def file_done(self, session_id, filename: str):
        with self._lock:
            st = self._state.get(self._key(session_id))
            if not st:
                return
            st["files_done"] += 1
            st["current_file"] = filename
            total = max(1, st["total_files"])
            self._set_percent(st, "parsing", st["files_done"] / total)
            st["message"] = f"Parsed {st['files_done']}/{total}: {filename[:60]}"

    def stage(self, session_id, stage: str, done: int = 0, total: int = 0,
              message: Optional[str] = None):
        with self._lock:
            st = self._state.get(self._key(session_id))
            if not st:
                return
            frac = (done / total) if total else 0.0
            self._set_percent(st, stage, frac)
            if message:
                st["message"] = message
            elif total:
                st["message"] = f"{stage.capitalize()} {done}/{total} chunks…"
            else:
                st["message"] = f"{stage.capitalize()}…"

    def finish(self, session_id, message: str = "Indexing complete"):
        with self._lock:
            st = self._state.get(self._key(session_id))
            if not st:
                return
            st.update({"stage": "done", "percent": 100, "done": True,
                       "message": message, "eta_seconds": 0})

    def fail(self, session_id, error: str):
        with self._lock:
            st = self._state.get(self._key(session_id))
            if not st:
                return
            st.update({"stage": "error", "done": True, "error": error,
                       "message": f"Indexing failed: {error[:200]}"})

    def get(self, session_id) -> Dict[str, Any]:
        with self._lock:
            st = self._state.get(self._key(session_id))
            if not st:
                return {"stage": "idle", "percent": 0, "done": True,
                        "message": "No indexing in progress."}
            return dict(st)

    def clear(self, session_id):
        with self._lock:
            self._state.pop(self._key(session_id), None)


progress = _ProgressRegistry()
