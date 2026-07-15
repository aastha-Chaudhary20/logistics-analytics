# Windows port — every change, one bundle

This folder is the CONSOLIDATED set of every file changed across all fix packs
(31 files), with Windows-specific patches applied. Copy it onto your windows
branch and you're at parity with the Mac setup in one commit.

## What's inside

    rag_system/api_server.py        newest: analytics, PDF POs, files[], progress,
                                    cancel, ledger, drafting backend, query log
    rag_system/main.py              cpu_fast profile (MiniLM reranker ON)
    rag_system/analytics/  (7)      engine, normalizer, router, po_generator,
                                    po_pdf, drafting_backend, __init__
    rag_system/ingestion/  (3)      file_router, indexed_ledger, chunk_dedup
    rag_system/utils/      (3)      query_log, cancellation, index_progress
    rag_system/rerankers/  (1)      minilm_reranker
    rag_system/pipelines/  (2)      indexing_pipeline, retrieval_pipeline
    backend/               (2)      server.py, database.py (WAL + name fix)
    src/components/        (6)      IndexForm + ui/{session-chat, quick-chat,
                                    conversation-page, generated-files,
                                    indexing-progress}
    config/drafting.json.example
    scan_fstring.py · test_excel_parse.py
    start_windows.bat · start_windows.ps1

## Windows-specific changes made in this bundle

1. **po_pdf.py — ₹ font on Windows.** The font search previously only looked
   in macOS/Linux paths, so on Windows every PO would silently fall back to
   "INR 55,000" text. Added C:\Windows\Fonts\arial.ttf and segoeui.ttf
   (both carry the ₹ glyph since Windows 8.1). Everything else in the code was
   already cross-platform (os.path.join throughout; SQLite WAL, threading,
   http.server all work identically on Windows).

2. **Startup scripts.** `RAG_CONFIG_MODE=cpu_fast python run_system.py` is
   bash-only. Use start_windows.bat (cmd) or start_windows.ps1 (PowerShell).

## Applying to the windows branch (git workflow)

From your repo root on the Windows machine (or anywhere):

    git checkout windows-main            # or whatever the branch is called
    git pull

    # copy this bundle's contents over the repo root, preserving paths
    #   PowerShell:
    Copy-Item -Recurse -Force .\windows_port\* .\
    #   (or drag-merge the folders in Explorer — same result)

    git add -A
    git commit -m "Port StellarKeep fixes: analytics lane, Excel parsing, PDF POs, progress, WAL, dedup, reranker"
    git push

Tip: if mac-main and windows-main share history, a cleaner long-term flow is
to commit these on main and `git merge main` into windows-main — then future
fixes flow with one merge instead of re-copying. The only permanent
divergence the windows branch needs is the .bat/.ps1 scripts.

## Setup on the Windows machine

    conda activate localgpt_env          # or your venv
    pip install pandas openpyxl duckdb reportlab sentence-transformers pyarrow

    # verify before first run (catches version/syntax issues under THAT python)
    python scan_fstring.py (Get-ChildItem -Recurse rag_system,backend -Filter *.py | % FullName)
    python test_excel_parse.py "path\to\EVN 3503 Report.xlsx"

    .\start_windows.bat

## Command translations (mac -> Windows PowerShell)

    rm -rf lancedb index_store/bm25 index_store/ledgers
      ->  Remove-Item -Recurse -Force lancedb, index_store\bm25, index_store\ledgers

    lsof -i :8001  /  kill -9 <PID>
      ->  netstat -ano | findstr :8001
          taskkill /PID <PID> /F

    tail -f logs/*.log
      ->  Get-Content logs\rag_api.log -Wait

    python -m rag_system.utils.query_log --unanswered     (same on both)

## Windows gotchas to expect (not bugs, just differences)

  • Ollama on Windows serves on the same http://localhost:11434 — no change,
    but install the Windows Ollama build and `ollama pull qwen3:4b` there.
  • No MPS on Windows: embeddings/reranker run pure CPU (or CUDA if the box
    has an NVIDIA GPU — torch will pick it up automatically; indexing gets
    dramatically faster if so).
  • Windows Defender sometimes slows first-time model loads (it scans the
    downloaded weights). Subsequent runs are normal.
  • Path length: if the repo lives deep in the filesystem, long report
    filenames can exceed 260 chars. Either enable long paths
    (LongPathsEnabled=1) or keep the repo near the drive root (C:\localGPT).
  • WAL sidecar files (chat_data.db-wal/-shm) appear next to the DB — normal,
    don't delete while running.
