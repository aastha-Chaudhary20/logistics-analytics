"""
rag_system/utils/cancellation.py

A tiny per-session cancellation registry so a running query can be stopped
mid-flight from another request (the "stop" button).

How it works:
  • Each streaming query registers a threading.Event under its session_id.
  • A POST /chat/cancel with that session_id sets the event.
  • The streaming pipeline's event_callback checks is_cancelled() at every phase
    boundary (analyzing → sub-queries → retrieving → reranking → answering) and
    raises CancelledQuery to stop cleanly.

Honest scope: cancellation takes effect at the NEXT checkpoint the pipeline
emits. A single long LLM token-generation with no intermediate callback can't
be interrupted mid-token from here; it stops at the following phase. For the
non-streaming /chat endpoint (no checkpoints) cancel can't interrupt an
in-flight call — use the streaming endpoint for a responsive stop button.
"""
import threading
from typing import Dict


class CancelledQuery(Exception):
    """Raised inside the streaming loop when a stop has been requested."""


_events: Dict[str, threading.Event] = {}
_lock = threading.Lock()


def begin(session_id: str) -> threading.Event:
    """Start (or reset) a cancellable operation for this session."""
    key = session_id or "_global"
    with _lock:
        ev = _events.get(key)
        if ev is None:
            ev = threading.Event()
            _events[key] = ev
        else:
            ev.clear()  # reset any stale cancel from a previous query
        return ev


def request_cancel(session_id: str) -> bool:
    """Signal the running operation for this session to stop.
    Returns True if there was something to cancel."""
    key = session_id or "_global"
    with _lock:
        ev = _events.get(key)
        if ev is None:
            return False
        ev.set()
        return True


def is_cancelled(session_id: str) -> bool:
    key = session_id or "_global"
    ev = _events.get(key)
    return bool(ev and ev.is_set())


def finish(session_id: str):
    """Clean up after an operation completes."""
    key = session_id or "_global"
    with _lock:
        _events.pop(key, None)
