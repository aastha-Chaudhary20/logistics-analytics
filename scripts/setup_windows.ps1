# StellarKeep AI — one-command tester setup (Windows PowerShell)
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
$ErrorActionPreference = "Stop"
Write-Host "=== StellarKeep AI setup (Windows) ==="

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  Write-Host "❌ Install Python 3.10+ first (python.org), tick 'Add to PATH'"; exit 1 }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  Write-Host "❌ Install Node 18+ first (nodejs.org)"; exit 1 }
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
  Write-Host "❌ Install Ollama for Windows first (ollama.com), then re-run"; exit 1 }

Write-Host "-> Pulling models (first time only, ~1GB)..."
ollama pull qwen3:0.6b
ollama pull nomic-embed-text

Write-Host "-> Python environment..."
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -q --upgrade pip
pip install -q pandas openpyxl duckdb pyarrow lancedb reportlab sentence-transformers rdflib requests numpy docling rank-bm25 matplotlib

Write-Host "-> Frontend dependencies..."
npm install --silent

Write-Host "-> Pre-flight checks..."
$pyfiles = Get-ChildItem -Recurse rag_system,backend -Filter *.py | Where-Object FullName -notmatch "__pycache__" | ForEach-Object FullName
python scan_fstring.py $pyfiles
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host ""
Write-Host "✅ Setup complete. Start with:"
Write-Host "   .\.venv\Scripts\Activate.ps1 ; .\start_windows.bat"
Write-Host "   then open http://localhost:3000"
