"""
rag_system/ingestion/file_router.py  (v4 — CPU-friendly, dependency-safe)

Fixes for "not able to read excel, parse it to JSON or Markdown":

  1. DEPENDENCY SAFETY. v3 did `import duckdb` at module top — if duckdb (or
     openpyxl for .xlsx) was missing, the import of the whole indexing pipeline
     failed, or Excel reads crashed into a garbage binary-text fallback.
     v4 lazy-imports everything, prints EXACTLY which package is missing, and
     still parses Excel -> Markdown/JSON even when DuckDB is absent
     (DuckDB is now an optional bonus, not a requirement).

  2. VISIBLE OUTPUT. Every parsed structured file is also written to
     `index_store/parsed/<file>.md` and `<file>.json` so you can OPEN the
     parsed result and verify what the RAG system sees. If parsing failed,
     these files won't exist — instant debugging.

  3. Row-level indexing kept from v3 (every row as "Column: value" text) so
     BM25/vector search can hit the exact row that answers a query.

Required:  pip install pandas openpyxl
Optional:  pip install duckdb           (adds the analytics warehouse)

`to_pages()` returns List[Tuple[markdown, metadata]] — same shape the indexing
pipeline already consumes.
"""
from typing import Any, Dict, List, Optional, Tuple
import json
import os
import re

STRUCTURED_EXT = {".csv", ".tsv", ".xlsx", ".xls", ".json", ".jsonl", ".parquet"}
DOCLING_EXT = {".docx", ".pptx", ".html", ".htm"}
TEXT_EXT = {".txt", ".md", ".markdown"}

ROWS_PER_PAGE = 8            # data rows per indexable "row group" page
MAX_ROW_PAGES_PER_TABLE = 400

# --------------------------------------------------------------------------- #
# Lazy, loud dependency handling
# --------------------------------------------------------------------------- #
_pd = None
def _pandas():
    global _pd
    if _pd is None:
        try:
            import pandas as pd
            _pd = pd
        except ImportError:
            raise ImportError(
                "MISSING DEPENDENCY: pandas is required to parse Excel/CSV. "
                "Run:  pip install pandas openpyxl"
            )
    return _pd


def _check_excel_engine():
    try:
        import openpyxl  # noqa: F401
        return True
    except ImportError:
        print("❌ MISSING DEPENDENCY: openpyxl is required for .xlsx files. "
              "Run:  pip install openpyxl")
        return False


def _duckdb_or_none():
    try:
        import duckdb
        return duckdb
    except ImportError:
        return None  # optional — parsing/Markdown/JSON still work


# --------------------------------------------------------------------------- #
# Type coercion & header detection
# --------------------------------------------------------------------------- #
def _coerce(col):
    pd = _pandas()
    cleaned = col.astype(str).str.replace(r"[,\u20b9$%\s]", "", regex=True).str.strip()
    num = pd.to_numeric(cleaned, errors="coerce")
    nn = col.notna().sum()
    if nn and num.notna().sum() / nn >= 0.8:
        return num, "number"
    try:
        dt = pd.to_datetime(col, errors="coerce", format="mixed")
        if nn and dt.notna().sum() / nn >= 0.8:
            return dt, "date"
    except Exception:
        pass
    return col.astype("object"), "text"


def _header_candidates(grid, scan=25, top_n=4):
    """Score potential header rows; return the best few candidates.
    Report-style sheets often contain decoy rows (legends, banners) above the
    real header, so we evaluate multiple candidates instead of trusting one."""
    scored = []
    for i in range(min(scan, len(grid))):
        nn = [c for c in grid[i] if c not in (None, "")]
        if len(nn) < 2:
            continue
        strish = sum(1 for c in nn if isinstance(c, str)) / len(nn)
        below = grid[i + 1:i + 6]
        below_filled = sum(1 for r in below if len([c for c in r if c not in (None, "")]) >= 2)
        if strish >= 0.5 and below_filled >= 1:
            scored.append((len(nn) * 2 + 3 * strish + below_filled, i))
    scored.sort(reverse=True)
    return [i for _, i in scored[:top_n]]


def _build_frame(grid, hi):
    pd = _pandas()
    width = len(grid[hi])
    header = [str(c).strip() if c not in (None, "") else f"col{j}" for j, c in enumerate(grid[hi])]
    seen: Dict[str, int] = {}
    for j, h in enumerate(header):
        seen[h] = seen.get(h, -1) + 1
        if seen[h]:
            header[j] = f"{h}_{seen[h]}"
    rows = [list(r)[:width] + [None] * (width - len(r)) for r in grid[hi + 1:]]
    df = pd.DataFrame(rows, columns=header).dropna(axis=1, how="all").dropna(axis=0, how="all")
    if df.shape[1] < 2 or df.shape[0] < 1:
        return None, 0.0
    types = []
    for c in df.columns:
        df[c], t = _coerce(df[c])
        types.append(t)
    fill = float(df.notna().mean().mean())
    named = sum(1 for c in df.columns if not str(c).startswith(("col", "nan")))
    # Quality: dense, typed, well-named tables win. This is what makes the
    # REAL header ("Item description / L1 Rate / ...") beat decoys like
    # ("Color Legend / Rank 1 / Rank 2") whose "data" is sparse junk.
    quality = fill * df.shape[1] + 1.5 * sum(t in ("number", "date") for t in types) + 0.5 * named
    conf = round(min(0.95, 0.35 + 0.45 * fill + (0.2 if any(t in ("number", "date") for t in types) else 0)), 2)
    return df, quality if conf > 0.3 else 0.0


def _frame_from_grid(grid):
    """Try every plausible header row; keep the frame with the best quality."""
    best_df, best_q, best_hi = None, 0.0, None
    for hi in _header_candidates(grid):
        df, q = _build_frame(grid, hi)
        if df is not None and q > best_q:
            best_df, best_q, best_hi = df, q, hi
    if best_df is None:
        return None, 0.0, None
    conf = round(min(0.95, 0.35 + 0.45 * float(best_df.notna().mean().mean())), 2)
    return best_df, conf, best_hi


def _report_metadata(grid, header_idx):
    """Extract the key-value block ABOVE the table (Event ID, Event Name,
    Timeline, Participants, ...) — report-style sheets carry crucial context
    there that a pure table extraction would silently drop."""
    def _is_filled(c):
        if c is None or c == "":
            return False
        if isinstance(c, float) and c != c:  # NaN
            return False
        return True

    lines = []
    stop = header_idx if header_idx is not None else min(25, len(grid))
    for row in grid[:stop]:
        filled = [(j, c) for j, c in enumerate(row) if _is_filled(c)]
        if not filled:
            continue
        vals = [str(c).strip() for _, c in filled]
        if len(filled) == 1:
            lines.append(vals[0])                      # banner/title line
        elif len(filled) <= 4:
            label, rest = vals[0], " | ".join(vals[1:])
            lines.append(f"{label}: {rest}")           # key-value line
        # >4 filled cells above the header is likely a decoy header row; skip
    return lines


def _safe_name(s: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", str(s).lower()).strip("_")[:60] or "t"


def _fmt_cell(v: Any) -> str:
    pd = _pandas()
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    return str(v).strip()


# --------------------------------------------------------------------------- #
# Page builders
# --------------------------------------------------------------------------- #
def _table_card(name: str, df, source: str, truncated: bool) -> str:
    out = [
        f"# Data table: {name} (from file: {source})",
        f"Rows: {len(df)}  Columns: {df.shape[1]}",
        "Columns: " + ", ".join(map(str, df.columns)),
        "",
        "Sample rows:",
        df.head(5).to_string(index=False),
    ]
    num = df.select_dtypes("number")
    if num.shape[1]:
        out += ["", "Numeric summary (min / mean / max):",
                num.describe().loc[["min", "mean", "max"]].to_string()]
    if truncated:
        out += ["", f"NOTE: only the first {ROWS_PER_PAGE * MAX_ROW_PAGES_PER_TABLE} rows "
                    f"were indexed as searchable text."]
    return "\n".join(out)


def _row_pages(name: str, df, source: str) -> List[Tuple[str, Dict[str, Any]]]:
    """Every row rendered as 'Column: value'. This is what makes exact lookups
    ('price of 14ft truck') retrievable by BM25 + vectors."""
    pages: List[Tuple[str, Dict[str, Any]]] = []
    cols = [str(c) for c in df.columns]
    records = df.to_dict("records")
    for start in range(0, len(records), ROWS_PER_PAGE):
        if len(pages) >= MAX_ROW_PAGES_PER_TABLE:
            break
        batch = records[start:start + ROWS_PER_PAGE]
        lines = [f"## Rows {start + 1}-{start + len(batch)} of data table '{name}' (file: {source})", ""]
        for i, rec in enumerate(batch):
            lines.append(f"### Row {start + i + 1}")
            for c in cols:
                val = _fmt_cell(rec.get(c))
                if val != "":
                    lines.append(f"- {c}: {val}")
            lines.append("")
        pages.append(("\n".join(lines), {
            "source": source, "kind": "table_rows", "table": name,
            "row_start": start + 1, "row_end": start + len(batch),
        }))
    return pages


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
class FileRouter:
    def __init__(self, document_converter=None,
                 db_path: str = "./index_store/structured.duckdb",
                 parsed_dir: str = "./index_store/parsed"):
        self.document_converter = document_converter
        self.db_path = db_path
        self.parsed_dir = parsed_dir
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        os.makedirs(parsed_dir, exist_ok=True)
        self._docling = None

    # -- structured ---------------------------------------------------------
    def _read_frames(self, path: str) -> List[Tuple[str, Any]]:
        """Return list of (label, DataFrame). Excel -> one per usable sheet."""
        pd = _pandas()
        ext = os.path.splitext(path)[1].lower()
        stem = _safe_name(os.path.splitext(os.path.basename(path))[0])
        frames: List[Tuple[str, Any]] = []

        if ext in (".xlsx", ".xls"):
            if ext == ".xlsx" and not _check_excel_engine():
                return []
            book = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
            for sheet, raw in book.items():
                grid = raw.values.tolist()
                df, conf, hi = _frame_from_grid(grid)
                if df is None:
                    print(f"  ⚠️  Sheet '{sheet}' in {os.path.basename(path)}: no usable table, skipped.")
                    continue
                meta_lines = _report_metadata(grid, hi)
                label = stem if len(book) == 1 else f"{stem}__{_safe_name(sheet)}"
                print(f"  ✅ Sheet '{sheet}': header row {hi}, {len(df)} rows x {df.shape[1]} cols (confidence {conf})"
                      + (f", {len(meta_lines)} report-info lines" if meta_lines else ""))
                frames.append((label, df, meta_lines))

        elif ext in (".csv", ".tsv"):
            sep = "\t" if ext == ".tsv" else None
            raw = pd.read_csv(path, header=None, dtype=object, sep=sep, engine="python")
            grid = raw.values.tolist()
            df, conf, hi = _frame_from_grid(grid)
            meta_lines = _report_metadata(grid, hi) if df is not None else []
            if df is None:
                df = pd.read_csv(path, sep=sep, engine="python")
            frames.append((stem, df, meta_lines))

        elif ext == ".parquet":
            frames.append((stem, pd.read_parquet(path), []))

        elif ext in (".json", ".jsonl"):
            try:
                df = pd.read_json(path, lines=(ext == ".jsonl"))
                if hasattr(df, "shape") and df.shape[1] >= 1:
                    frames.append((stem, df, []))
            except Exception:
                pass  # not tabular JSON — will be indexed as plain text below

        return frames

    def _write_sidecars(self, name: str, df, pages_md: str):
        """Write parsed .md and .json next to the index so the user can VERIFY
        exactly what the RAG system extracted from the spreadsheet."""
        md_path = os.path.join(self.parsed_dir, f"{name}.md")
        json_path = os.path.join(self.parsed_dir, f"{name}.json")
        try:
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(pages_md)
            records = json.loads(df.to_json(orient="records", date_format="iso"))
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump({"table": name, "rows": len(records),
                           "columns": list(map(str, df.columns)),
                           "data": records}, f, ensure_ascii=False, indent=2, default=str)
            print(f"  📝 Parsed output written: {md_path} and {json_path}")
        except Exception as e:
            print(f"  ⚠️  Could not write parsed sidecar files: {e}")

    def _load_structured(self, path: str) -> List[Tuple[str, Dict[str, Any]]]:
        source = os.path.basename(path)
        try:
            frames = self._read_frames(path)
        except Exception as e:
            print(f"  ❌ Structured read failed for {source}: {type(e).__name__}: {e}")
            frames = []

        if not frames:
            print(f"  ❌ {source}: could not extract any table. "
                  f"Check dependencies (pip install pandas openpyxl) and file integrity.")
            return [(f"# File: {source}\nThe system could not parse this structured file. "
                     f"Its contents are NOT available for question answering.",
                     {"source": source, "kind": "review"})]

        duckdb = _duckdb_or_none()
        con = duckdb.connect(self.db_path) if duckdb else None
        if con is None:
            print("  ℹ️  duckdb not installed — skipping analytics warehouse "
                  "(Markdown/JSON parsing and search indexing still work). "
                  "Optional: pip install duckdb")

        pages: List[Tuple[str, Dict[str, Any]]] = []
        for label, df, meta_lines in frames:
            name = _safe_name(label)
            if meta_lines:
                info_md = (f"# Report information (file: {source}, table: {name})\n\n"
                           + "\n".join(f"- {l}" for l in meta_lines))
                pages.append((info_md, {"source": source, "kind": "report_info", "table": name}))
            if con is not None:
                con.register("t", df)
                con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM t')
                con.unregister("t")

            truncated = len(df) > ROWS_PER_PAGE * MAX_ROW_PAGES_PER_TABLE
            card = _table_card(name, df, source, truncated)
            rows = _row_pages(name, df, source)
            pages.append((card, {"source": source, "kind": "table_card", "table": name}))
            pages.extend(rows)
            info_parts = [p for p, m in pages if m.get("kind") == "report_info" and m.get("table") == name]
            self._write_sidecars(name, df, "\n\n".join(info_parts + [card] + [p for p, _ in rows]))
        if con is not None:
            con.close()
        print(f"  ✅ {source}: {len(pages)} indexable pages "
              f"({sum(1 for _, m in pages if m['kind'] == 'table_rows')} row pages)")
        return pages

    # -- unstructured -------------------------------------------------------
    def _docling_convert(self, path) -> List[Tuple[str, Dict[str, Any]]]:
        if self._docling is None:
            from docling.document_converter import DocumentConverter
            self._docling = DocumentConverter()
        doc = self._docling.convert(path).document
        return [(doc.export_to_markdown(), {"source": os.path.basename(path), "kind": "document"})]

    # -- entry point --------------------------------------------------------
    def to_pages(self, file_path: str) -> List[Tuple[str, Dict[str, Any]]]:
        ext = os.path.splitext(file_path)[1].lower()
        if ext in STRUCTURED_EXT:
            return self._load_structured(file_path)
        if ext in TEXT_EXT:
            text = open(file_path, errors="ignore").read()
            return [(text, {"source": os.path.basename(file_path), "kind": "document"})]
        pages: List[Tuple[str, Dict[str, Any]]] = []
        if self.document_converter is not None:
            try:
                pages = self.document_converter.convert_to_markdown(file_path) or []
            except Exception as e:
                print(f"  ⚠️  document_converter failed on {file_path}: {e}")
        if pages:
            return pages
        if ext in DOCLING_EXT:
            return self._docling_convert(file_path)
        print(f"  ⚠️  Unsupported / unreadable file, skipped: {file_path}")
        return []
