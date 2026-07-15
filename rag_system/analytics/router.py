"""
rag_system/analytics/router.py

Classifies an incoming question into one of three lanes:

  'analytics' -> AnalyticsEngine (SQL over procurement_events; computed numbers)
  'document'  -> PO / purchase-document generation (structured fetch + template)
  'rag'       -> existing retrieval pipeline (single-fact lookups, prose Qs)

Deliberately rule-based: on an air-gapped CPU box an extra LLM classification
call costs seconds; keywords cover procurement phrasing well. An optional
llm_fn can arbitrate ambiguous cases.
"""
import re
from typing import Callable, Optional

_ANALYTIC = re.compile(
    r"\b(total|sum|average|avg|mean|median|min(imum)?|max(imum)?|lowest|highest|"
    r"cheapest|most expensive|rank|top \d+|compare|comparison|across|overall|"
    r"spend|spending|trend|history|historical|over time|per kg|cost per|"
    r"how many|count|distribution|pattern|insights?|performance|"
    r"summary (of )?(all |our )?(vendors?|suppliers?|transporters?|events?)?|"
    r"by (vendor|supplier|transporter|route|month|year)|"
    r"(lane|route|corridor)s? (analysis|frequency|pattern|summary)|"
    r"(spend|complete|full) (report|analysis)|concentration|dependency)\b", re.I)

_DOCUMENT = re.compile(
    r"\b(draft|create|generate|prepare|write|make)\b.{0,40}\b(po|purchase order|"
    r"purchase document|rfq|rfp|award letter|contract|comparative statement)\b", re.I)

_LOOKUP_HINT = re.compile(r"\bEVN[\s_-]?\d{3,5}\b", re.I)

# Fields the canonical procurement_events table answers EXACTLY. If the user
# names an event AND one of these, SQL is strictly better than fuzzy retrieval:
# instant, and immune to the "retrieved the wrong event's chunks" failure mode.
_STRUCTURED_FIELD = re.compile(
    r"\b(l1|l2|l3|price|rate|cost|freight|amount|vendor|transporter|supplier|"
    r"winner|awarded|quantity|qty|weight|participants?|timeline|route|origin|"
    r"destination)\b", re.I)


def route(question: str, llm_fn: Optional[Callable[[str], str]] = None) -> str:
    q = question.strip()
    if _DOCUMENT.search(q):
        return "document"
    if _ANALYTIC.search(q):
        return "analytics"
    if _LOOKUP_HINT.search(q):
        # "L1 price of EVN 3503" -> analytics (exact SQL row lookup).
        # "what does the note in EVN 3503 say" -> rag (prose question).
        if _STRUCTURED_FIELD.search(q):
            return "analytics"
        return "rag"
    if llm_fn:
        out = (llm_fn(
            "Classify the question as exactly one word - analytics (aggregation/"
            "comparison/trend over many records), document (drafting a PO or "
            "purchase document), or rag (single-fact lookup).\n"
            f"Question: {q}\nAnswer:") or "").strip().lower()
        if out in ("analytics", "document", "rag"):
            return out
    return "rag"
