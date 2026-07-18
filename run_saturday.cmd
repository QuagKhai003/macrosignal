@echo off
rem MacroSignal weekly batch launcher (batch 6.4) - invoked by Windows Task
rem Scheduler every Saturday (main) and Sunday (retry; the run is idempotent:
rem same-week short-circuits make a rerun fill only what is missing).
rem Output lands in logs\ (gitignored) - one file per run date.
cd /d D:\Code\macrosignal
if not exist logs mkdir logs
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i
set LOGFILE=logs\run-%TODAY%.log
rem -u: unbuffered, so the log fills live and a killed run still leaves a trail
.venv\Scripts\python.exe -u weekly_run.py --full >> "%LOGFILE%" 2>&1
.venv\Scripts\python.exe -u weekly_run.py --health >> "%LOGFILE%" 2>&1
