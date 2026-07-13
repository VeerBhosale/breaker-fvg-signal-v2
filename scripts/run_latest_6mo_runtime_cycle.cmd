@echo off
setlocal

set "RUN_ID=%~1"
if "%RUN_ID%"=="" set "RUN_ID=v2_runtime_cycle_original_latest_6mo_manual"

set "REPO_DIR=%~dp0.."
set "BREAKER_FVG_BREAKER_BASED_ROOT=D:\Coding\Python Codes\Newtest\Breaker_Based"
set "BREAKER_FVG_REFERENCE_ENGINE=D:\Coding\Python Codes\Newtest\Breaker_Based\breaker_fvg_dashboard_export.py"
set "BREAKER_FVG_V1_SIGNAL_MODEL_ROOT=D:\Coding\Python Codes\Newtest\Breaker_Based\signal_model"
set "BREAKER_FVG_SHORT_SIGNAL_MODEL_ROOT=D:\Coding\Python Codes\Newtest\Breaker_Based\signal_model_short"

cd /d "%REPO_DIR%"

"C:\Users\veerb\AppData\Local\Programs\Python\Python310\python.exe" scripts\v2_run_runtime_cycle.py ^
  --candles-dir "D:\Coding\Python Codes\breaker-fvg-signal-v2\data\raw\v2_incremental_candle_store" ^
  --run-id "%RUN_ID%" ^
  --max-input-candles 750 ^
  --workers 4 ^
  --step-timeout-seconds 900 ^
  --ticker-timeout-seconds 1500 ^
  --notional-capital-inr 1000000

exit /b %ERRORLEVEL%
