#!/usr/bin/env python3
"""
test_excel_parse.py — Standalone check: "can the system read my Excel file?"

Run from the repo root, no servers needed:

    python test_excel_parse.py path/to/your_file.xlsx

It will:
  1. Check dependencies (pandas / openpyxl / duckdb) and tell you what's missing.
  2. Parse the file exactly like the indexing pipeline does.
  3. Print the detected sheets, headers, row counts.
  4. Write the parsed Markdown and JSON to index_store/parsed/ so you can open
     and verify what the RAG system will "see".

If this script succeeds but chat answers are still wrong, the problem is in
retrieval config, not Excel parsing.
"""
import sys
import os

def check_deps():
    ok = True
    for pkg, why in [("pandas", "REQUIRED for Excel/CSV parsing"),
                     ("openpyxl", "REQUIRED for .xlsx files"),
                     ("duckdb", "optional (analytics warehouse)")]:
        try:
            __import__(pkg)
            print(f"  ✅ {pkg} installed")
        except ImportError:
            level = "❌" if "REQUIRED" in why else "ℹ️ "
            print(f"  {level} {pkg} MISSING — {why}.  pip install {pkg}")
            if "REQUIRED" in why:
                ok = False
    return ok


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    path = sys.argv[1]
    if not os.path.exists(path):
        print(f"❌ File not found: {path}")
        sys.exit(1)

    print("=== 1. Dependency check ===")
    if not check_deps():
        print("\nInstall the missing REQUIRED packages above, then re-run.")
        sys.exit(1)

    print("\n=== 2. Parsing (same code path as indexing) ===")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from rag_system.ingestion.file_router import FileRouter

    router = FileRouter()
    pages = router.to_pages(path)

    print(f"\n=== 3. Result: {len(pages)} indexable pages ===")
    row_pages = [p for p in pages if p[1].get("kind") == "table_rows"]
    cards = [p for p in pages if p[1].get("kind") == "table_card"]
    review = [p for p in pages if p[1].get("kind") == "review"]

    for _, meta in cards:
        print(f"  📊 table: {meta['table']}")
    print(f"  📄 {len(row_pages)} row pages (these make individual rows searchable)")

    if review:
        print("\n❌ PARSING FAILED — the file could not be converted to a table.")
        print("   Fix the dependency/file issue reported above and re-run.")
        sys.exit(1)
    if not row_pages:
        print("\n⚠️  No row pages generated — the sheet may be empty or not tabular.")
        sys.exit(1)

    print("\n=== 4. Preview of first searchable row page ===")
    print(row_pages[0][0][:800])
    print(f"\n✅ SUCCESS. Open index_store/parsed/ to see the full Markdown/JSON output.")
    print("   Now (re)index this file through the app and row-level answers will work.")


if __name__ == "__main__":
    main()
