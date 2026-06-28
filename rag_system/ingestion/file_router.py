"""
rag_system/ingestion/file_router.py

Dispatches each uploaded file by *shape* so localGPT stops forcing everything
through the PDF path:

  STRUCTURED  (csv, tsv, xlsx, xls, json, jsonl, parquet)
      -> loaded as real tables into a persistent DuckDB warehouse (for analytics
         and task skills), AND returned as a compact "table card" so the table is
         searchable in normal RAG.
  PDF                      -> existing PDFConverter (docling, unchanged).
  docx / pptx / html       -> docling multi-format converter.
  txt / md                 -> read directly.

`to_pages()` returns the same List[Tuple[markdown, metadata]] shape the indexing
pipeline already consumes, so wiring it in is a one-line change.

The DuckDB path is GLOBAL by default (not per-session), which is what makes the
structured memory cumulative across uploads.
"""
from typing import Any, Dict, List, Tuple
import json
import os
import re

import duckdb
import pandas as pd

STRUCTURED_EXT = {".csv", ".tsv", ".xlsx", ".xls", ".json", ".jsonl", ".parquet"}
DOCLING_EXT = {".docx", ".pptx", ".html", ".htm"}
TEXT_EXT = {".txt", ".md", ".markdown"}
CONF_THRESHOLD = 0.55


def _coerce(col: pd.Series):
    cleaned = col.astype(str).str.replace(r"[,\u20b9$%]", "", regex=True).str.strip()
    num = pd.to_numeric(cleaned, errors="coerce")
    nn = col.notna().sum()
    if nn and num.notna().sum() / nn >= 0.8:
        return num, "number"
    try:
        dt = pd.to_datetime(col, errors="coerce")
        if nn and dt.notna().sum() / nn >= 0.8:
            return dt, "date"
    except Exception:
        pass
    return col.astype("object"), "text"


def _detect_header(grid, scan=20):
    best_i, best = None, -1.0
    for i in range(min(scan, len(grid))):
        nn = [c for c in grid[i] if c not in (None, "")]
        if len(nn) < 2:
            continue
        strish = sum(1 for c in nn if isinstance(c, str)) / len(nn)
        below = grid[i + 1:i + 6]
        below_filled = sum(1 for r in below if len([c for c in r if c not in (None, "")]) >= 2)
        score = len(nn) + 3 * strish + below_filled
        if strish >= 0.5 and below_filled >= 1 and score > best:
            best, best_i = score, i
    return best_i


def _frame_from_grid(grid):
    hi = _detect_header(grid)
    if hi is None:
        return None, 0.0
    width = len(grid[hi])
    header = [str(c).strip() if c not in (None, "") else f"col{j}" for j, c in enumerate(grid[hi])]
    seen = {}
    for j, h in enumerate(header):
        seen[h] = seen.get(h, -1) + 1
        if seen[h]:
            header[j] = f"{h}_{seen[h]}"
    rows = [list(r)[:width] + [None] * (width - len(r)) for r in grid[hi + 1:]]
    df = pd.DataFrame(rows, columns=header).dropna(axis=1, how="all").dropna(axis=0, how="all")
    if df.shape[1] < 2 or df.shape[0] < 2:
        return None, 0.25
    types = []
    for c in df.columns:
        df[c], t = _coerce(df[c]); types.append(t)
    fill = float(df.notna().mean().mean())
    conf = round(0.35 + 0.45 * fill + (0.2 if any(t in ("number", "date") for t in types) else 0), 2)
    return df, conf


def _safe_name(s):
    return re.sub(r"[^a-z0-9_]", "_", s.lower()).strip("_")[:60] or "t"


def _table_card(name, df):
    out = [f"# Data table: {name}", f"Rows: {len(df)}  Columns: {df.shape[1]}",
           "Columns: " + ", ".join(map(str, df.columns)), "", "Sample rows:",
           df.head(3).to_string(index=False)]
    num = df.select_dtypes("number")
    if num.shape[1]:
        out += ["", "Numeric summary:", num.describe().loc[["min", "mean", "max"]].to_string()]
    return "\n".join(out)


class FileRouter:
    def __init__(self, document_converter=None, db_path="./index_store/structured.duckdb"):
        # `document_converter` is the repo's existing converter (has .convert_to_markdown).
        self.document_converter = document_converter
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._docling = None  # lazy multi-format docling fallback

    # -- structured ---------------------------------------------------------
    def _load_structured(self, path) -> List[Tuple[str, Dict[str, Any]]]:
        ext = os.path.splitext(path)[1].lower()
        base = os.path.splitext(os.path.basename(path))[0]
        frames = []  # (label, df)
        if ext in (".csv", ".tsv"):
            sep = "\t" if ext == ".tsv" else None
            raw = pd.read_csv(path, sep=sep, header=None, dtype=str, engine="python",
                              on_bad_lines="skip", keep_default_na=False).replace("", None)
            df, conf = _frame_from_grid(raw.values.tolist())
            if df is not None and conf >= CONF_THRESHOLD:
                frames.append((base, df))
        elif ext in (".xlsx", ".xls"):
            from openpyxl import load_workbook
            wb = load_workbook(path, read_only=True, data_only=True)
            for ws in wb.worksheets:
                grid = [list(r) for r in ws.iter_rows(values_only=True)]
                if not grid:
                    continue
                df, conf = _frame_from_grid(grid)
                if df is not None and conf >= CONF_THRESHOLD:
                    frames.append((f"{base}__{ws.title}", df))
            wb.close()
        elif ext in (".json", ".jsonl"):
            recs = ([json.loads(l) for l in open(path) if l.strip()] if ext == ".jsonl"
                    else json.load(open(path)))
            if isinstance(recs, dict):
                recs = recs.get("data", recs.get("results", [recs]))
            if isinstance(recs, list) and recs and all(isinstance(x, dict) for x in recs):
                jdf = pd.json_normalize(recs)
                if jdf.shape[0] >= 2 and jdf.shape[1] >= 2:  # else: nested/degenerate -> review
                    frames.append((base, jdf))
        elif ext == ".parquet":
            frames.append((base, pd.read_parquet(path)))

        if not frames:
            return [(f"# Unparsed file: {os.path.basename(path)}\n"
                     "Flagged for review: could not detect a clean table.",
                     {"source": os.path.basename(path), "kind": "review"})]

        con = duckdb.connect(self.db_path)
        pages = []
        for label, df in frames:
            name = _safe_name(label)
            con.register("t", df)
            con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM t')
            con.unregister("t")
            pages.append((_table_card(name, df),
                          {"source": os.path.basename(path), "kind": "table_card", "table": name}))
        con.close()
        return pages

    # -- unstructured -------------------------------------------------------
    def _docling_convert(self, path) -> List[Tuple[str, Dict[str, Any]]]:
        if self._docling is None:
            from docling.document_converter import DocumentConverter
            self._docling = DocumentConverter()  # default = all supported formats
        doc = self._docling.convert(path).document
        return [(doc.export_to_markdown(), {"source": os.path.basename(path), "kind": "document"})]

    # -- entry point --------------------------------------------------------
    def to_pages(self, file_path: str) -> List[Tuple[str, Dict[str, Any]]]:
        ext = os.path.splitext(file_path)[1].lower()
        # 1) Structured data -> DuckDB warehouse + searchable card
        if ext in STRUCTURED_EXT:
            return self._load_structured(file_path)
        # 2) Plain text -> read directly
        if ext in TEXT_EXT:
            text = open(file_path, errors="ignore").read()
            return [(text, {"source": os.path.basename(file_path), "kind": "document"})]
        # 3) Everything else (pdf, docx, pptx, html, images) -> repo converter first
        pages = []
        if self.document_converter is not None:
            try:
                pages = self.document_converter.convert_to_markdown(file_path) or []
            except Exception as e:
                print(f"  ⚠️  document_converter failed on {file_path}: {e}")
                pages = []
        if pages:
            return pages
        # 4) Fallback: multi-format docling for office docs the repo converter missed
        if ext in DOCLING_EXT:
            return self._docling_convert(file_path)
        print(f"  ⚠️  Unsupported / unreadable file, skipped: {file_path}")
        return []
