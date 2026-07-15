
"""
rag_system/ingestion/indexed_ledger.py

Tracks which source files have already been indexed into each vector table, so
"add more files" only processes the NEW ones instead of re-indexing everything.

Stored as a small JSON per table under index_store/ledgers/<table>.json:
    {"files": {"<abs_path>": {"name": "...", "ts": "..."}}, ...}

Keyed by absolute path AND basename, so a file re-uploaded under a new temp
path (common when the frontend copies to shared_uploads) is still recognised
by its original name and skipped.

Usage in the /index handler:
    from rag_system.ingestion.indexed_ledger import IndexedLedger
    ledger = IndexedLedger(table_name)
    new_paths = ledger.filter_new(file_paths)      # only the ones not yet done
    ...run indexing on new_paths...
    ledger.mark_indexed(new_paths)                 # record them
"""
from typing import Dict, List
import json
import os
from datetime import datetime, timezone

LEDGER_DIR = os.path.join("index_store", "ledgers")


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(name))[:120] or "default"


class IndexedLedger:
    def __init__(self, table_name: str):
        os.makedirs(LEDGER_DIR, exist_ok=True)
        self.table = _safe(table_name or "default")
        self.path = os.path.join(LEDGER_DIR, f"{self.table}.json")
        self._data: Dict[str, Dict] = {}
        self._names: set = set()
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f).get("files", {})
                self._names = {v.get("name", "") for v in self._data.values()}
            except Exception:
                self._data, self._names = {}, set()

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"table": self.table, "files": self._data}, f,
                          ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ Could not write ledger {self.path}: {e}")

    def is_indexed(self, path: str) -> bool:
        ap = os.path.abspath(path)
        if ap in self._data:
            return True
        # match by basename too (handles re-upload under a new temp path)
        base = os.path.basename(path)
        # strip common "<uuid>_" prefix the app adds
        stripped = base.split("_", 1)[-1] if "_" in base[:40] else base
        return base in self._names or stripped in self._names

    def filter_new(self, paths: List[str]) -> List[str]:
        """Return only paths not already indexed (deduped, order preserved)."""
        seen, out = set(), []
        for p in paths:
            ap = os.path.abspath(p)
            if ap in seen:
                continue
            seen.add(ap)
            if not self.is_indexed(p):
                out.append(p)
        return out

    def mark_indexed(self, paths: List[str]):
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for p in paths:
            base = os.path.basename(p)
            name = base.split("_", 1)[-1] if "_" in base[:40] else base
            self._data[os.path.abspath(p)] = {"name": name, "ts": ts}
            self._names.add(name)
        self._save()

    def all_indexed(self) -> List[str]:
        return sorted(self._names)

    def count(self) -> int:
        return len(self._data)
