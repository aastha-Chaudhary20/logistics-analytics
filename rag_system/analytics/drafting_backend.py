"""
rag_system/analytics/drafting_backend.py

Chooses WHO writes the prose layer of a purchase order / purchase document:
the local model (air-gapped, default) or the Claude API (opt-in, requires the
company to explicitly allow it). The NUMBERS never depend on this choice —
every figure in a PO still comes from the SQL row in po_generator.py. This
module only decides who phrases the surrounding language.

## Air-gapped is the hard default
If nothing is configured, DraftingBackend.local() is used and no network call
of any kind is possible. Claude is only reachable if BOTH:
  1. the config file explicitly sets drafting.backend = "claude", AND
  2. an API key is present (ANTHROPIC_API_KEY env var or config).
Missing either -> silently falls back to local. This is deliberate: a stray
config typo should never cause data to leave the building.

## What actually leaves the machine when Claude is selected
ONLY the pre-extracted, already-computed fields (vendor name, route, qty,
rate, weight, date) that draft_purchase_order() pulls from procurement_events
— the same numbers that would appear in the final document. Raw bid files,
full item descriptions beyond what's in the PO, other vendors' bids, and
anything not already destined for the printed document are NEVER sent. This
is enforced by scrub_for_cloud(), not left to prompt discipline.

## Configuration (config.yaml or environment)
    drafting:
      backend: "local"            # "local" (default) | "claude"
      claude_model: "claude-sonnet-4-6"
      # api key: set ANTHROPIC_API_KEY in the environment, never in the yaml

Per-request override (still gated by the same allow-list): a caller can pass
backend="claude" to draft_document(), but if the config backend is "local"
the request is downgraded to local and the response says so — a per-request
flag can loosen nothing the config forbids.
"""
from typing import Any, Callable, Dict, Optional
import os

ALLOWED_CLOUD_FIELDS = {
    "event_id", "event_name", "origin", "destination", "vendor",
    "quantity", "rate", "weight_kg", "route", "buyer", "po_number", "date",
}


class DraftingBackend:
    """Resolves to a callable(prompt: str) -> str, chosen by config."""

    def __init__(self, config: Optional[Dict[str, Any]] = None,
                local_llm_fn: Optional[Callable[[str], str]] = None):
        cfg = (config or {}).get("drafting", {})
        self.configured_backend = (cfg.get("backend") or "local").strip().lower()
        self.claude_model = cfg.get("claude_model", "claude-sonnet-4-6")
        self.local_llm_fn = local_llm_fn
        self._api_key = os.getenv("ANTHROPIC_API_KEY")

    # ------------------------------------------------------------- resolve
    def resolve(self, requested_backend: Optional[str] = None) -> str:
        """Which backend will actually be used, honoring the air-gapped rule:
        cloud requires BOTH config opt-in AND an API key; anything else -> local."""
        wants_cloud = (requested_backend or self.configured_backend or "local").lower() == "claude"
        config_allows_cloud = self.configured_backend == "claude"
        if wants_cloud and config_allows_cloud and self._api_key:
            return "claude"
        return "local"

    def status(self) -> Dict[str, Any]:
        return {
            "configured_backend": self.configured_backend,
            "cloud_available": bool(self.configured_backend == "claude" and self._api_key),
            "note": ("Fully air-gapped: drafting uses the local model only." 
                    if self.configured_backend != "claude" else
                    "Cloud drafting enabled: pre-extracted PO fields (vendor, "
                    "route, qty, rate) may be sent to Claude. Source files are not.")
        }

    # ------------------------------------------------------------- generate
    def generate(self, prompt: str, *, backend: Optional[str] = None) -> Dict[str, Any]:
        used = self.resolve(backend)
        if used == "claude":
            try:
                text = self._claude_generate(prompt)
                return {"text": text, "backend": "claude", "downgraded": False}
            except Exception as e:
                # Cloud failure never blocks drafting — fall back to local.
                text = self._local_generate(prompt)
                return {"text": text, "backend": "local", "downgraded": True,
                        "downgrade_reason": f"Claude call failed ({e}); used local model."}
        text = self._local_generate(prompt)
        downgraded = bool(backend and backend.lower() == "claude" and used == "local")
        out = {"text": text, "backend": "local", "downgraded": downgraded}
        if downgraded:
            out["downgrade_reason"] = ("Cloud drafting is not enabled in this deployment's "
                                       "config (drafting.backend != 'claude', or no API key). "
                                       "Set drafting.backend: claude and ANTHROPIC_API_KEY to enable it.")
        return out

    def _local_generate(self, prompt: str) -> str:
        if not self.local_llm_fn:
            return ""
        try:
            return (self.local_llm_fn(prompt) or "").strip()
        except Exception:
            return ""

    def _claude_generate(self, prompt: str) -> str:
        # Imported lazily so the anthropic package is only required when the
        # company has actually opted into cloud drafting.
        import anthropic
        client = anthropic.Anthropic(api_key=self._api_key)
        resp = client.messages.create(
            model=self.claude_model,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        return "".join(parts).strip()


def scrub_for_cloud(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Whitelist filter: only fields already destined for the printed PO may
    be included in a cloud prompt. Anything else (raw descriptions, other
    bids, file paths, notes) is dropped even if accidentally passed in."""
    return {k: v for k, v in fields.items() if k in ALLOWED_CLOUD_FIELDS and v is not None}


def build_cover_note_prompt(fields: Dict[str, Any]) -> str:
    """The ONLY thing ever sent to Claude for PO drafting: a short, scrubbed
    fact sheet, with an explicit instruction not to invent anything."""
    safe = scrub_for_cloud(fields)
    facts = "\n".join(f"- {k}: {v}" for k, v in safe.items())
    return (
        "Write ONE short, professional cover sentence for a purchase order, "
        "using ONLY the facts below. Do not invent any detail not listed. "
        "Do not restate the amounts (they appear in the document body).\n\n"
        f"{facts}\n\nCover sentence:"
    )
