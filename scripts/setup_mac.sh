#!/bin/bash
# StellarKeep AI — one-command tester setup (macOS)
#   bash scripts/setup_mac.sh
set -e
echo "=== StellarKeep AI setup (macOS) ==="

command -v python3 >/dev/null || { echo "❌ Install Python 3.10+ first: https://python.org"; exit 1; }
command -v node >/dev/null || { echo "❌ Install Node 18+ first: https://nodejs.org"; exit 1; }

if ! command -v ollama >/dev/null; then
  echo "→ Installing Ollama…"
  curl -fsSL https://ollama.com/install.sh | sh
fi
echo "→ Pulling models (first time only, ~1GB)…"
ollama pull qwen3:0.6b
ollama pull nomic-embed-text

echo "→ Python environment…"
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q pandas openpyxl duckdb pyarrow lancedb reportlab sentence-transformers rdflib requests numpy docling rank-bm25 matplotlib

echo "→ Frontend dependencies…"
npm install --silent

echo "→ Pre-flight checks…"
python scan_fstring.py $(find rag_system backend -name "*.py" | grep -v __pycache__) || exit 1

echo ""
echo "✅ Setup complete. Start with:"
echo "   source .venv/bin/activate && RAG_CONFIG_MODE=cpu_fast python run_system.py"
echo "   then open http://localhost:3000"
