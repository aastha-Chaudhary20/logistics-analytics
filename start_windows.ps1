# start_windows.ps1 — PowerShell variant.
# Run:  powershell -ExecutionPolicy Bypass -File .\start_windows.ps1

$env:RAG_CONFIG_MODE = "cpu_fast"
$env:GENERATION_MODEL = "qwen3:4b"
# $env:DRAFTING_CONFIG_PATH = "config\drafting.json"

python run_system.py
