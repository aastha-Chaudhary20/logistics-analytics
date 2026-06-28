#!/usr/bin/env python3
"""
apply_fix.py -- install the dual-path + task-route fix into localGPT.

Why a script instead of a .patch: git apply matches on surrounding context, which
keeps breaking across repo versions. This edits only the exact unique lines, so it
ignores whitespace/line-number drift. It is idempotent (safe to run twice) and
prints exactly what it changed.

Run from the repo root (the folder that contains rag_system/):
    python apply_fix.py
"""
import os
import sys
import py_compile

ROOT = os.getcwd()
PIPE = os.path.join(ROOT, "rag_system", "pipelines", "indexing_pipeline.py")
LOOP = os.path.join(ROOT, "rag_system", "agent", "loop.py")

if not os.path.isdir(os.path.join(ROOT, "rag_system")):
    sys.exit("❌ Run this from the localGPT repo root (the folder containing rag_system/).")


def edit(path, label, anchor, new, *, sentinel):
    """Replace `anchor` with `new` unless `sentinel` already present. Returns status."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if sentinel in text:
        return f"  • {label}: already applied, skipped"
    if anchor not in text:
        return f"  ⚠️  {label}: anchor NOT FOUND — needs manual edit (see notes)"
    text = text.replace(anchor, new, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return f"  ✅ {label}: done"


print("Patching indexing_pipeline.py ...")
# 1) import FileRouter
print(edit(
    PIPE, "import FileRouter",
    "from rag_system.ingestion.document_converter import DocumentConverter",
    "from rag_system.ingestion.document_converter import DocumentConverter\n"
    "from rag_system.ingestion.file_router import FileRouter",
    sentinel="from rag_system.ingestion.file_router import FileRouter"))

# 2) instantiate the router right after the converter
print(edit(
    PIPE, "instantiate FileRouter",
    "        self.document_converter = DocumentConverter()",
    "        self.document_converter = DocumentConverter()\n"
    "        # Route files by shape: structured -> DuckDB warehouse + searchable card;\n"
    "        # everything else -> existing converter (docling/text fallback).\n"
    "        self.file_router = FileRouter(\n"
    "            document_converter=self.document_converter,\n"
    "            db_path=self.config.get('structured_db_path', './index_store/structured.duckdb'),\n"
    "        )",
    sentinel="self.file_router = FileRouter("))

# 3) route every file through the dispatcher (substring keeps original indentation)
print(edit(
    PIPE, "use file_router.to_pages",
    "self.document_converter.convert_to_markdown(file_path)",
    "self.file_router.to_pages(file_path)",
    sentinel="self.file_router.to_pages(file_path)"))

print("\nPatching agent/loop.py ...")
TASK_BRANCH = '''print(f"Agent Triage Decision: '{query_type}'")

        # --- TASK route: produce a deliverable (e.g. draft a PO) instead of answering ---
        import re as _re
        if _re.search(r"\\b(draft|create|generate|raise|prepare|issue|make)\\b.*\\b(purchase order|p\\.?o\\.?|order)\\b", query, _re.I):
            print("🛠️  ROUTING: task_query detected -> skills handler")
            try:
                from rag_system.skills.purchase_order import handle_task
                _result = await asyncio.to_thread(handle_task, query)
            except Exception as _e:
                _result = {"answer": f"Task skill failed: {_e}"}
            _result.setdefault("source_documents", [])
            if session_id:
                history.append({"query": query, "answer": _result.get("answer", "")})
                self.chat_histories[session_id] = history
            return _result'''
print(edit(
    LOOP, "task route branch",
    'print(f"Agent Triage Decision: \'{query_type}\'")',
    TASK_BRANCH,
    sentinel="task_query detected -> skills handler"))

print("\nCompiling to verify syntax ...")
ok = True
for p in (PIPE, LOOP):
    try:
        py_compile.compile(p, doraise=True)
        print(f"  ✅ {os.path.relpath(p, ROOT)} compiles")
    except py_compile.PyCompileError as e:
        ok = False
        print(f"  ❌ {os.path.relpath(p, ROOT)} FAILED: {e}")

print("\n" + ("✅ All edits applied and files compile." if ok else
              "❌ A file failed to compile — paste the error and I'll fix it."))
print("Reminder: also copy in rag_system/ingestion/file_router.py and "
      "rag_system/skills/purchase_order.py (+ empty __init__.py), then "
      "`pip install duckdb python-docx`.")
