# StellarKeep AI — Tester Distribution Guide

How to share this with people for testing. Two paths, pick per tester:

  A. DOCKER (recommended for anyone who has/can install Docker Desktop)
     -> one command, identical environment everywhere
  B. SETUP SCRIPT (for testers without Docker)
     -> one script installs deps and starts natively

Both paths need Ollama installed NATIVELY on the tester's machine (not in
Docker). Reason: Ollama inside Docker on Mac cannot use the Apple GPU, making
the model 3-5x slower. Native Ollama keeps Metal (Mac) / CUDA (Windows).

──────────────────────────────────────────────────────────────────
WHAT YOU (Aastha) DO ONCE
──────────────────────────────────────────────────────────────────
1. Copy the contents of this pack into your repo:
       docker/Dockerfile
       docker/docker-compose.yml
       docker/requirements-docker.txt
       .dockerignore                 (repo root)
       scripts/setup_mac.sh
       scripts/setup_windows.ps1
       TESTER_GUIDE.md               (this file, or trim it)
2. Commit + push (both branches, or main then merge — the files are identical
   on both platforms).
3. Share the GitHub repo link with testers. If the repo is private, add them
   as collaborators or create a fresh public "stellarkeep-beta" repo.

──────────────────────────────────────────────────────────────────
TESTER INSTRUCTIONS — PATH A: DOCKER
──────────────────────────────────────────────────────────────────
Prereqs: Docker Desktop, Ollama (ollama.com), git.

    git clone <repo-url> && cd <repo>
    ollama pull qwen3:0.6b
    ollama pull nomic-embed-text
    docker compose -f docker/docker-compose.yml up --build

First build takes several minutes (installs Python + Node deps, builds the
frontend). Then open http://localhost:3000.

Data (indexes, reports, uploads) persists in Docker volumes between runs.
Stop with Ctrl+C; `docker compose -f docker/docker-compose.yml down` to
remove containers (volumes survive).

Linux testers: the compose file already includes the
`host.docker.internal:host-gateway` mapping they need.

──────────────────────────────────────────────────────────────────
TESTER INSTRUCTIONS — PATH B: SETUP SCRIPT (no Docker)
──────────────────────────────────────────────────────────────────
Prereqs: Python 3.10+, Node 18+, Ollama, git.

macOS:
    git clone <repo-url> && cd <repo>
    bash scripts/setup_mac.sh
    source .venv/bin/activate && RAG_CONFIG_MODE=cpu_fast python run_system.py

Windows (PowerShell):
    git clone <repo-url> ; cd <repo>
    powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
    .\.venv\Scripts\Activate.ps1 ; .\start_windows.bat

Then open http://localhost:3000.

──────────────────────────────────────────────────────────────────
WHAT TO TELL TESTERS TO TRY (put this in your message to them)
──────────────────────────────────────────────────────────────────
1. Create an index, drag in a few sourcing report .xlsx files, watch the
   progress bar.
2. Ask: "tell me the L1 rate of EVN <id from your file>"
3. Ask: "give me a consolidated analysis report"
4. Ask: "draft a purchase order for EVN <id>" -> download the PDF
5. Try to break it: ask about an event you never uploaded (it should refuse,
   not invent), switch chats mid-answer, re-upload the same file twice.

Ask them to send you: what they asked, what they expected, what they got.
(Your query log at logs/query_log.jsonl records every query + which lane
answered it — collect that file from testers for the full picture.)

──────────────────────────────────────────────────────────────────
HONEST LIMITS OF THIS DISTRIBUTION
──────────────────────────────────────────────────────────────────
• This is NOT a double-click installer. Testers need git + Ollama (+ Docker
  or Python/Node). That's the right tradeoff for a technical beta; a true
  packaged desktop app (Electron + bundled runtime + bundled models) is a
  separate multi-week packaging project — worth doing only after tester
  feedback validates the product.
• I could not run `docker build` in my environment — the Dockerfile and
  compose file are syntax-validated and follow your repo's actual layout
  (run_system.py entrypoint, OLLAMA_HOST env respected by your code), but
  expect to iterate once on the first real build. Most likely first-build
  issue: a Python package missing from requirements-docker.txt — add it and
  rebuild.
• Testers' machines need ~8GB RAM for the models. On low-RAM machines set
  GENERATION_MODEL=qwen3:0.6b (the compose default already does this).
