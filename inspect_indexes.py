#!/usr/bin/env python3
"""
inspect_indexes.py — run from your localGPT repo root:

    python inspect_indexes.py

Prints, for every index you've created:
  • how many document chunks it has (RAG / prose questions read these)
  • which files are in it
and separately the SHARED analytics table (number questions read this).

This shows why "switched index, can't answer" happens:
  - RAG documents are PER-INDEX. Index B can't answer prose questions about
    files that were only added to index A.
  - Analytics rows are SHARED across all indexes, so number questions should
    work from any index — if they don't, the analytics table is just empty.
"""
import sys
sys.path.insert(0, ".")


def main():
    try:
        from rag_system.analytics.index_inspector import inspect_all
    except Exception as e:
        print(f"Could not import inspector: {e}")
        sys.exit(1)

    # get the app's database to list indexes
    db = None
    try:
        from backend.database import ChatDatabase
        db = ChatDatabase()
    except Exception as e:
        print(f"(could not open ChatDatabase, will still try analytics: {e})")

    if db is None:
        from rag_system.analytics.index_inspector import _analytics_summary
        a = _analytics_summary()
        print("SHARED ANALYTICS TABLE (all indexes can query this):")
        print(f"  rows: {a.get('rows', 0)} · events: {a.get('events', 0)}")
        if a.get("event_ids"):
            print(f"  events: {', '.join(str(e) for e in a['event_ids'][:40])}")
        return

    data = inspect_all(db)

    print("=" * 66)
    print("SHARED ANALYTICS  (number questions — spend, L1 rate — read this)")
    print("=" * 66)
    a = data.get("analytics_shared", {})
    print(f"  rows: {a.get('rows', 0)} · distinct events: {a.get('events', 0)}")
    if a.get("event_ids"):
        print(f"  event ids: {', '.join(str(e) for e in a['event_ids'][:40])}")
    if a.get("rows", 0) == 0:
        print("  ⚠️  EMPTY — number questions will fail in EVERY index until you")
        print("      re-index files (this is likely your problem).")

    print()
    print("=" * 66)
    print("PER-INDEX DOCUMENTS  (prose questions read only the CURRENT index)")
    print("=" * 66)
    idxs = data.get("indexes", [])
    if not idxs:
        print("  (no indexes found)")
    for ix in idxs:
        if ix.get("error"):
            print(f"  {ix['error']}")
            continue
        chunks = ix.get("rag_chunks", 0)
        flag = "  ⚠️ EMPTY (re-index files into it)" if chunks == 0 else ""
        print(f"\n  • {ix.get('name') or '(unnamed)'}  [{ix.get('vector_table')}]")
        print(f"      {chunks} document chunks{flag}")
        for s in ix.get("rag_sources", [])[:12]:
            print(f"        - {s}")

    print("\n" + "=" * 66)
    print("READING THIS:")
    print("  • A number question failing everywhere -> shared analytics is empty.")
    print("  • A prose question working in one index but not another -> the file")
    print("    is only in the first index. Add it to the other, or ask in the")
    print("    index that has it.")
    print("=" * 66)


if __name__ == "__main__":
    main()
