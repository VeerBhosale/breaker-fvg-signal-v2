@echo off
setlocal

cd /d "D:\Coding\Python Codes"

set RUN_ID=v2_replay_original_fresh_178_tail300_parallel_burnin
set STDOUT=Newtest\Breaker_Based\signal_model_v2\logs\%RUN_ID%_stdout.log
set STDERR=Newtest\Breaker_Based\signal_model_v2\logs\%RUN_ID%_stderr.log

python Newtest\Breaker_Based\signal_model_v2\scripts\v2_run_replay_batch.py ^
  --candles-dir Newtest\Breaker_Based\signal_model_v2\data\raw\v2_yfinance_ingest_original_179_1h_730d_full_burnin ^
  --run-id %RUN_ID% ^
  --continue-on-error ^
  --resume ^
  --max-input-candles 300 ^
  --step-timeout-seconds 120 ^
  --ticker-timeout-seconds 240 ^
  --workers 4 ^
  1> "%STDOUT%" 2> "%STDERR%"

exit /b %ERRORLEVEL%
