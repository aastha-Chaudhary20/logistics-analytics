@echo off
REM start_windows.bat — start StellarKeep with the CPU-optimized profile.
REM (The mac command `RAG_CONFIG_MODE=cpu_fast python run_system.py` is bash
REM syntax; on Windows env vars are set with `set`, hence this script.)

set RAG_CONFIG_MODE=cpu_fast
set GENERATION_MODEL=qwen3:4b
REM Optional overrides:
REM set QUERY_LOG_PATH=logs\query_log.jsonl
REM set DRAFTING_CONFIG_PATH=config\drafting.json

python run_system.py
