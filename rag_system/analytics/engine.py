"""
rag_system/analytics/engine.py

The analytics lane: answers aggregation questions ("total spend by vendor",
"cheapest cost/kg lane", "price history for FBD") by RUNNING SQL over the
canonical procurement_events table — the model never invents a number.

Design for a weak local model, air-gapped:

  1. BUILT-IN INTENTS FIRST. The common procurement questions are answered by
     hand-written, correct SQL templates matched by keywords. Zero LLM calls,
     instant, always right. This is the reliability floor.
  2. TEXT-TO-SQL FALLBACK. Anything else: the local LLM writes DuckDB SQL from
     the schema. The SQL is validated (SELECT-only, single statement, no
     writes), executed read-only, and retried ONCE with the error message if
     it fails. If it still fails, we say so instead of guessing.
  3. NARRATION LAST. The LLM may phrase the result, but every figure it is
     given comes from the executed rows.

Usage:
    eng = AnalyticsEngine("./index_store/structured.duckdb", llm_fn=ollama_generate)
    result = eng.ask("total spend by transporter this year")
    # result = {"mode": "intent|llm_sql", "sql": ..., "columns": [...], "rows": [...]}
"""
from typing import Any, Callable, Dict, List, Optional
import os
import re

TABLE = "procurement_events"

SCHEMA_DOC = f"""Table {TABLE} — one row per awarded line item in a sourcing event:
  event_id VARCHAR            e.g. 'EVN 3356'
  event_name VARCHAR          full negotiation title
  origin VARCHAR              route start, e.g. 'ECE'
  destination VARCHAR         route end, e.g. 'BINA, MADHYA PRADESH SAGAR'
  start_time, end_time TIMESTAMP   negotiation window
  participants INTEGER        number of bidders
  item_description VARCHAR
  vehicle_qty DOUBLE
  l1_rate DOUBLE              lowest (winning) quoted rate, INR
  l1_transporter VARCHAR      winning vendor
  final_price DOUBLE          awarded price, INR
  material_weight_kg DOUBLE
  cost_per_kg DOUBLE          final_price / weight (precomputed)
  source_file VARCHAR
"""

_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|copy|pragma|install|load)\b", re.I)


def _safe(sql: str) -> Optional[str]:
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        return None
    if not re.match(r"^\s*(select|with)\b", s, re.I):
        return None
    if _FORBIDDEN.search(s):
        return None
    return s


class AnalyticsEngine:
    def __init__(self, db_path: str, llm_fn: Optional[Callable[[str], str]] = None):
        import duckdb
        self.db_path = db_path
        self.duckdb = duckdb
        self.llm_fn = llm_fn  # callable(prompt) -> completion text

    # ------------------------------------------------------------------ exec
    def _run(self, sql: str) -> Dict[str, Any]:
        try:
            con = self.duckdb.connect(self.db_path, read_only=True)
        except Exception:
            # a writer holds the file (e.g. indexing in progress) — fall back;
            # SQL is already validated SELECT-only so this stays safe
            con = self.duckdb.connect(self.db_path)
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(200)  # cap output
            return {"sql": sql, "columns": cols, "rows": rows}
        finally:
            con.close()

    # ------------------------------------------------------- built-in intents
    def _intent_sql(self, q: str) -> Optional[str]:
        ql = q.lower()
        has = lambda *w: all(x in ql for x in w)
        anyof = lambda *w: any(x in ql for x in w)

        # spend by month (buying pattern over time)
        if anyof("month", "monthly", "by month") or (anyof("pattern", "seasonal") and anyof("spend", "buy")):
            return (f"SELECT date_trunc('month', start_time) AS month, COUNT(*) AS events, "
                    f"SUM(final_price) AS total_spend_inr, AVG(cost_per_kg) AS avg_cost_per_kg "
                    f"FROM {TABLE} WHERE start_time IS NOT NULL "
                    f"GROUP BY 1 ORDER BY 1")
        # lane / route analysis (which corridors we buy most)
        if anyof("lane", "route", "corridor") and anyof("analysis", "frequency", "most", "top", "spend", "pattern", "summary"):
            return (f"SELECT origin, destination, COUNT(*) AS events, "
                    f"SUM(final_price) AS total_spend_inr, AVG(cost_per_kg) AS avg_cost_per_kg, "
                    f"MIN(cost_per_kg) AS best_cost_per_kg "
                    f"FROM {TABLE} WHERE origin <> '' "
                    f"GROUP BY 1,2 ORDER BY total_spend_inr DESC LIMIT 20")
        # vendor concentration (dependency risk)
        if anyof("concentration", "dependency", "share", "distribution") and anyof("vendor", "supplier", "transporter", "spend"):
            return (f"WITH v AS (SELECT l1_transporter AS vendor, SUM(final_price) AS spend "
                    f"FROM {TABLE} WHERE l1_transporter IS NOT NULL GROUP BY 1) "
                    f"SELECT vendor, spend, ROUND(100.0*spend/SUM(spend) OVER (), 1) AS pct_of_total_spend "
                    f"FROM v ORDER BY spend DESC")
        # spend by vendor
        if anyof("spend", "total") and anyof("vendor", "transporter", "supplier"):
            return (f"SELECT l1_transporter AS vendor, COUNT(*) AS events, "
                    f"SUM(final_price) AS total_spend_inr, AVG(final_price) AS avg_price_inr "
                    f"FROM {TABLE} WHERE l1_transporter IS NOT NULL "
                    f"GROUP BY 1 ORDER BY total_spend_inr DESC")
        # cheapest / most expensive lane by cost per kg
        if "kg" in ql and anyof("cheap", "lowest", "best", "highest", "expensive", "worst", "compare", "rank"):
            order = "ASC" if anyof("cheap", "lowest", "best") else "DESC"
            return (f"SELECT event_id, origin, destination, l1_transporter, final_price, "
                    f"material_weight_kg, cost_per_kg FROM {TABLE} "
                    f"WHERE cost_per_kg IS NOT NULL ORDER BY cost_per_kg {order} LIMIT 15")
        # price / rate history for a route or keyword
        if anyof("history", "trend", "over time", "historical"):
            return (f"SELECT event_id, start_time, origin, destination, l1_transporter, "
                    f"l1_rate, final_price, cost_per_kg FROM {TABLE} "
                    f"WHERE start_time IS NOT NULL ORDER BY start_time ASC")
        # vendor insight / summary
        if anyof("insight", "summary", "performance") and anyof("vendor", "transporter", "supplier"):
            return (f"SELECT l1_transporter AS vendor, COUNT(*) AS wins, "
                    f"SUM(final_price) AS total_awarded_inr, "
                    f"MIN(cost_per_kg) AS best_cost_per_kg, AVG(cost_per_kg) AS avg_cost_per_kg "
                    f"FROM {TABLE} WHERE l1_transporter IS NOT NULL "
                    f"GROUP BY 1 ORDER BY wins DESC")
        # how many events / files
        if anyof("how many", "count") and anyof("event", "file", "report"):
            return (f"SELECT COUNT(DISTINCT event_id) AS events, "
                    f"COUNT(DISTINCT source_file) AS files, COUNT(*) AS line_items FROM {TABLE}")
        # single OR multi event lookup ("compare EVN 3356 and 3503")
        ids = re.findall(r"\bEVN[\s_-]?(\d{3,5})\b", q, re.I)
        # also catch bare numbers after the first EVN ("EVN 3356 and 3503")
        if ids:
            tail = re.findall(r"\b(\d{3,5})\b", q)
            ids = list(dict.fromkeys(ids + [t for t in tail if t in q]))
        if ids:
            clauses = " OR ".join([f"event_id ILIKE 'EVN%{i}'" for i in dict.fromkeys(ids)])
            return (f"SELECT event_id, origin, destination, l1_transporter, l1_rate, "
                    f"final_price, material_weight_kg, cost_per_kg, event_name "
                    f"FROM {TABLE} WHERE {clauses} ORDER BY event_id, l1_rate")
        return None

    # -------------------------------------------------------- LLM text-to-SQL
    def _llm_sql(self, q: str, prev_error: str = "") -> Optional[str]:
        if not self.llm_fn:
            return None
        prompt = (
            "You write DuckDB SQL. Reply with ONLY one SELECT statement, no prose, "
            "no markdown fences.\n\n" + SCHEMA_DOC +
            "\nRules: single SELECT/WITH statement; use ILIKE '%..%' for text matching; "
            "LIMIT 50 unless aggregating.\n"
            f"Question: {q}\n" +
            (f"Your previous SQL failed with: {prev_error}\nFix it.\n" if prev_error else "") +
            "SQL:"
        )
        out = self.llm_fn(prompt) or ""
        out = re.sub(r"^```(sql)?|```$", "", out.strip(), flags=re.M).strip()
        return _safe(out)

    # ------------------------------------------------------------------- ask
    def ask(self, question: str) -> Dict[str, Any]:
        sql = self._intent_sql(question)
        mode = "intent"
        if sql is None:
            sql, mode = self._llm_sql(question), "llm_sql"
        if sql is None:
            return {"mode": "unsupported", "error":
                    "Couldn't map this question to a query. Try naming a vendor, "
                    "route, event ID, or metric (spend, cost per kg, history)."}
        try:
            res = self._run(sql)
        except Exception as e:
            if mode == "llm_sql":
                sql2 = self._llm_sql(question, prev_error=str(e)[:300])
                if sql2:
                    try:
                        res = self._run(sql2)
                        res["mode"] = mode
                        return res
                    except Exception as e2:
                        return {"mode": mode, "sql": sql2, "error": str(e2)[:300]}
            return {"mode": mode, "sql": sql, "error": str(e)[:300]}
        res["mode"] = mode
        return res

    # ---------------------------------------------------------- spend report
    def spend_report(self, out_path: Optional[str] = None) -> str:
        """One-command 'complete analysis': runs the full intent set and writes
        a markdown spend report. Every figure is computed SQL — the local model
        is not involved, so the report is exact and audit-friendly. Optionally
        narrate it afterwards with narration_prompt for an executive summary."""
        def q(sql):
            try:
                r = self._run(sql)
                return r["columns"], r["rows"]
            except Exception as e:
                return [], [("query failed:", str(e)[:120])]

        def md_table(cols, rows, fmt_money=()):
            if not rows:
                return "_no data_\n"
            def cell(c, v):
                if v is None: return ""
                if c in fmt_money and isinstance(v, (int, float)): return f"₹{v:,.0f}"
                if isinstance(v, float): return f"{v:,.2f}"
                return str(v)
            out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
            out += ["| " + " | ".join(cell(c, v) for c, v in zip(cols, r)) + " |" for r in rows[:15]]
            return "\n".join(out) + "\n"

        money = ("total_spend_inr", "spend", "total_awarded_inr", "final_price", "avg_price_inr")
        c, r = q(f"SELECT COUNT(DISTINCT event_id), COUNT(DISTINCT source_file), COUNT(*), "
                 f"SUM(final_price), MIN(start_time), MAX(start_time) FROM {TABLE}")
        ev, files, items, spend, dmin, dmax = r[0]
        cq, rq = q(f"SELECT COUNT(*) FILTER (WHERE material_weight_kg IS NULL) AS no_weight, "
                   f"COUNT(*) FILTER (WHERE start_time IS NULL) AS no_date, "
                   f"COUNT(*) FILTER (WHERE l1_transporter IS NULL) AS no_vendor FROM {TABLE}")
        nw, nd, nv = rq[0]

        sections = [
            ("Spend by vendor", self._run(self._intent_sql("total spend by vendor"))),
            ("Vendor concentration (dependency risk)", self._run(self._intent_sql("vendor spend concentration"))),
            ("Monthly buying pattern", self._run(self._intent_sql("spend by month"))),
            ("Top lanes by spend", self._run(self._intent_sql("lane analysis top routes"))),
            ("Cheapest lanes (₹/kg)", self._run(self._intent_sql("cheapest cost per kg"))),
            ("Supplier performance", self._run(self._intent_sql("supplier insights summary"))),
        ]
        parts = [
            "# Procurement Spend Report",
            f"_Basis: {ev} events · {files} files · {items} line items · "
            f"period {str(dmin)[:10]} → {str(dmax)[:10]}. "
            f"All figures computed from the indexed data; nothing model-generated._",
            f"\n**Total awarded spend: ₹{(spend or 0):,.0f}**\n",
        ]
        for title, res in sections:
            parts.append(f"## {title}\n" + md_table(res["columns"], res["rows"], fmt_money=money))
        parts.append("## Data-quality notes\n"
                     f"- {nw} line item(s) missing material weight → excluded from ₹/kg metrics\n"
                     f"- {nd} missing event dates → excluded from monthly pattern\n"
                     f"- {nv} missing vendor names → excluded from vendor tables\n"
                     "Improving extraction for these files will sharpen the analysis.")
        report = "\n".join(parts)
        if out_path:
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(report)
        return report

    # ------------------------------------------------- consolidated analysis
    def event_analysis_report(self, out_path: Optional[str] = None,
                              llm_fn=None) -> str:
        """Gemini/Claude-style consolidated analysis across ALL indexed events —
        but with every figure computed by SQL, so the totals can never
        contradict the tables. Optional llm_fn writes the 'Strategic
        Observations' prose from a facts sheet of computed values only."""
        def q(sql):
            r = self._run(sql)
            return r["rows"]

        rows = q(f"""
            SELECT event_id, event_name, origin, destination, l1_transporter,
                   vehicle_qty, l1_rate, l2_rate, n_bids, route_total,
                   participants, start_time
            FROM {TABLE}
            WHERE l1_rate IS NOT NULL OR final_price IS NOT NULL
            ORDER BY start_time NULLS LAST, event_id""")
        if not rows:
            return "No awarded line items indexed yet — index the report files first."

        DASH, ARROW = "\u2014", "\u2192"   # hoisted: py<3.12 forbids backslashes inside f-string expressions
        def inr(v):
            return f"\u20b9{float(v):,.0f}" if v is not None else DASH

        def _n(v):  # NaN-safe number
            return v if isinstance(v, (int, float)) and v == v and v is not None else None
        total_spend = sum(_n(r[9]) or 0 for r in rows)
        total_vehicles = sum(int(_n(r[5]) or 0) for r in rows)
        n_events = len({r[0] for r in rows})

        # per-event table
        ev_lines = ["| Event | Route | Qty | Winning Vendor (L1) | L1 Rate | Route Total |",
                    "|---|---|---|---|---|---|"]
        for r in rows:
            route = f"{r[2] or DASH} {ARROW} {r[3] or DASH}"
            ev_lines.append(f"| {r[0]} | {route} | {int(_n(r[5]) or 1)} | "
                            f"{r[4] or DASH} | {inr(r[6])} | {inr(r[9])} |")

        # vendor share + wins (percentages against the REAL total)
        vend = {}
        for r in rows:
            v = r[4] or "(unknown)"
            d = vend.setdefault(v, {"spend": 0.0, "wins": 0})
            d["spend"] += _n(r[9]) or 0; d["wins"] += 1
        share_lines = ["| Vendor | Awarded Spend | % of Total | L1 Wins |", "|---|---|---|---|"]
        for v, d in sorted(vend.items(), key=lambda kv: -kv[1]["spend"]):
            pct = 100.0 * d["spend"] / total_spend if total_spend else 0
            share_lines.append(f"| {v} | {inr(d['spend'])} | {pct:.1f}% | {d['wins']} |")

        # computed insights
        insights = []
        big = max(rows, key=lambda r: _n(r[9]) or 0)
        insights.append(f"Largest corridor by spend: {big[0]} ({big[2]} \u2192 {big[3]}) at "
                        f"{inr(big[9])} \u2014 {100.0*(big[9] or 0)/total_spend:.1f}% of total.")
        margins = [(r, 100.0*(r[7]-r[6])/r[6]) for r in rows if _n(r[6]) and _n(r[7])]
        if margins:
            tight = min(margins, key=lambda x: x[1])
            insights.append(f"Most contested award: {tight[0][0]} \u2014 L2 missed L1 by only "
                            f"{tight[1]:.1f}% ({inr(tight[0][7])} vs {inr(tight[0][6])}).")
        # repeated routes: same origin+destination in different events
        by_route = {}
        for r in rows:
            by_route.setdefault((str(r[2]).strip().lower(), str(r[3]).strip().lower()), []).append(r)
        for (o, d), rs in by_route.items():
            evs = sorted({x[0] for x in rs})
            if len(evs) > 1:
                rates = sorted([(x[11], x[0], x[6]) for x in rs if x[6]],
                               key=lambda t: (t[0] is None, t[0]))
                if len(rates) >= 2 and rates[0][2] and rates[-1][2]:
                    delta = rates[0][2] - rates[-1][2]
                    word = "saving" if delta > 0 else "increase"
                    insights.append(
                        f"Repeated lane {rs[0][2]} \u2192 {rs[0][3]}: negotiated in {', '.join(evs)}; "
                        f"rate moved {inr(rates[0][2])} \u2192 {inr(rates[-1][2])} "
                        f"({word} of {inr(abs(delta))} on the same requirement).")
        low_comp = [r for r in rows if (r[10] or 0) <= 1]
        if low_comp:
            insights.append(f"{len(low_comp)} award(s) had a single participant \u2014 no price "
                            f"competition: {', '.join(sorted({r[0] for r in low_comp}))}.")

        parts = [
            "# Consolidated Procurement Analysis",
            f"_Basis: {n_events} events \u00b7 {len(rows)} awarded line items \u00b7 "
            f"{total_vehicles} vehicles. Every figure computed from indexed data._",
            f"\n**Total awarded logistics cost: {inr(total_spend)}**\n",
            "## Event-by-event awards\n" + "\n".join(ev_lines),
            "\n## Vendor share & standings\n" + "\n".join(share_lines),
            "\n## Computed insights\n" + "\n".join(f"- {i}" for i in insights),
        ]

        # optional LLM narrative — facts in, prose out, numbers verbatim
        if llm_fn:
            facts = "\n".join(insights)
            try:
                prose = llm_fn(
                    "You are a procurement analyst. Using ONLY these computed facts, "
                    "write a short 'Strategic Observations' section (3-5 sentences, "
                    "professional, no new numbers):\n" + facts)
                if prose and prose.strip():
                    parts.append("\n## Strategic observations\n" + prose.strip())
            except Exception:
                pass

        report = "\n".join(parts)
        if out_path:
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(report)
        return report

    # ------------------------------------------------------------- narration
    def narration_prompt(self, question: str, result: Dict[str, Any]) -> str:
        """Feed this to the local LLM to phrase the answer. All numbers are in
        the rows; instruct the model to use ONLY them."""
        table = "\n".join(
            [" | ".join(map(str, result.get("columns", [])))] +
            [" | ".join("" if v is None else str(v) for v in r) for r in result.get("rows", [])[:30]]
        )
        return (
            "Answer the user's question using ONLY the query result below. "
            "Quote numbers exactly as shown; do not compute new figures; "
            "if the result is empty say the data doesn't contain it.\n\n"
            f"Question: {question}\n\nQuery result:\n{table}\n\nAnswer:"
        )
