@echo off
REM ============================================================
REM  Win Disk Cleanup - Web App launcher (ASCII only)
REM  RULES (same as template_admin.bat):
REM    1. ASCII only. Chinese text belongs to the Python side.
REM    2. NEVER use chcp - breaks cmd byte offsets.
REM    3. No parentheses inside if (...) blocks.
REM    4. Use where /q + if not errorlevel 1 instead of >nul.
REM    5. Use goto label chains, not nested if blocks.
REM  Open http://127.0.0.1:5053 after launch. Ctrl+C to stop.
REM  For ProgramData / powercfg / Dism steps, right-click
REM  this file -> Run as administrator.
REM ============================================================

setlocal
set "PY="
set "PORT=5053"

where /q py
if not errorlevel 1 set "PY=py -3"
if defined PY goto HAVE_PY

where /q python
if not errorlevel 1 set "PY=python"
if defined PY goto HAVE_PY

if exist "C:\Python313\python.exe" set "PY=C:\Python313\python.exe"
if defined PY goto HAVE_PY

if exist "C:\Python312\python.exe" set "PY=C:\Python312\python.exe"
if defined PY goto HAVE_PY

echo [ERROR] Python not found. Install Python 3.8+ and rerun.
pause
goto END

:HAVE_PY
cd /d "%~dp0"
echo Using interpreter: %PY%
echo Starting Win Disk Cleanup on http://127.0.0.1:%PORT%
echo Press Ctrl+C to stop.
%PY% app.py
echo.
echo Server stopped.
pause

:END
endlocal
