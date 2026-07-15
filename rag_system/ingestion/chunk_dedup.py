"""
rag_system/ingestion/chunk_dedup.py

Chunk-level SHA-256 deduplication before embedding/insert.

The IndexedLedger already skips re-uploaded FILES, but the same report often
arrives under a different name (the app prefixes every upload with a fresh
UUID), and different files can share boilerplate sections. Hashing each
chunk's text and skipping ones already indexed:
  • prevents double-indexed content from re-uploads under new names
  • shrinks the vector index (fewer embeddings to compute — faster on CPU)
  • removes duplicate chunks from retrieval, which otherwise crowd the
    top-k with copies of the same text and push out relevant chunks

Persistence: one append-only file of hex digests per table at
index_store/ledgers/<table>.chunks.txt — loads into a set at startup,
appends as new chunks are accepted. At ~65 bytes/hash, even 1M chunks is a
~65MB file; at this project's scale it's trivial.

Usage inside IndexingPipeline.run(), right after all_chunks is assembled:

    from rag_system.ingestion.chunk_dedup import ChunkDedup
    dedup = ChunkDedup(table_name)
    all_chunks, n_dupes = dedup.filter_new(all_chunks)
    ...
    # after successful vector indexing:
    dedup.commit()
"""
from typing import Any, Dict, List, Tuple
import hashlib
import os

LEDGER_DIR = os.path.join("index_store", "ledgers")


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(name))[:120] or "default"


def chunk_hash(text: str) -> str:
    # Normalize whitespace so trivial spacing differences don't defeat dedup.
    norm = " ".join((text or "").split()).lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


class ChunkDedup:
    def __init__(self, table_name: str):
        os.makedirs(LEDGER_DIR, exist_ok=True)
        self.path = os.path.join(LEDGER_DIR, f"{_safe(table_name or 'default')}.chunks.txt")
        self._seen: set = set()
        self._pending: List[str] = []
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    self._seen = {line.strip() for line in f if line.strip()}
            except Exception:
                self._seen = set()

    def filter_new(self, chunks: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
        """Return (unique_new_chunks, duplicate_count). Also dedups WITHIN the
        batch (two files in one upload sharing a section)."""
        out: List[Dict[str, Any]] = []
        dupes = 0
        for c in chunks:
            h = chunk_hash(c.get("text", ""))
            if h in self._seen:
                dupes += 1
                continue
            # Mark seen in-memory immediately (covers within-batch duplicates
            # and later batches in the same process); disk persistence still
            # waits for commit() after a successful index.
            self._seen.add(h)
            self._pending.append(h)
            out.append(c)
        if dupes:
            print(f"🧬 Chunk dedup: skipped {dupes} duplicate chunk(s); "
                  f"{len(out)} unique chunk(s) proceed to embedding.")
        return out, dupes

    def commit(self):
        """Persist hashes of the chunks that were actually indexed. Call AFTER
        vector indexing succeeds, so a failed run doesn't poison the ledger
        on disk (in-memory marks reset naturally on process restart)."""
        if not self._pending:
            return
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write("\n".join(self._pending) + "\n")
            self._pending = []
        except Exception as e:
            print(f"⚠️ Could not persist chunk dedup ledger: {e}")
