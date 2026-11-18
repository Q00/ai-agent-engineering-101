@echo off
rem Starts keepalive.py (which runs the market kit) if it is not running yet.
rem Task Scheduler "ChooMarket" runs this at logon. MARKET_OWNER (claim code) and TELEGRAM_* are read from the user's
rem environment (setx); nothing secret is in this file. Output: logs\start_market.log
setlocal
set "ROOT=C:\Users\108\Desktop\AX\ai-agent-engineering-101"
set "W=%ROOT%\submissions\25512092\week-08"
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "PYTHONUTF8=1"
set "PATH=%ROOT%\.venv\Scripts;%PATH%"
set "LOG=%W%\logs\start_market.log"

echo %date% %time% start_market >> "%LOG%"
if not defined MARKET_OWNER (
  echo %date% %time% MARKET_OWNER is not set in the user environment: keepalive not started >> "%LOG%"
  goto watch
)

rem tools\is_running.ps1: exit 1 = already running (or the check failed) -> skip; never a second copy
powershell -NoProfile -ExecutionPolicy Bypass -File "%W%\tools\is_running.ps1" keepalive.py
if errorlevel 1 (
  echo %date% %time% keepalive already running: skipped >> "%LOG%"
) else (
  cd /d "%ROOT%"
  start "keepalive" /min "%PY%" -X utf8 submissions/25512092/week-08/keepalive.py
  echo %date% %time% keepalive started >> "%LOG%"
)

:watch
rem tools\patch35_watch.py is no longer started: its checks moved into the restock hook (alerts.forum_check) on 11/06
endlocal
exit /b 0
