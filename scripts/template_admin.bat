@echo off
REM ============================================================
REM  Windows cleanup helper - template
REM  RULES (learned the hard way, do not break them):
REM    1. ASCII only. No CJK in this file. Chinese text belongs
REM       to the Python side via SetConsoleOutputCP(65001).
REM    2. NEVER use chcp - it breaks cmd byte offsets and cmd
REM       then executes leftover "nul" as a command.
REM    3. NEVER use parentheses inside an if (...) block, even
REM       inside quotes - cmd parses them as block structure.
REM    4. Use "where /q" + "if not errorlevel 1" instead of ">nul".
REM    5. Use goto label chains, not nested if blocks.
REM    6. Right-click -> Run as administrator for the elevated
REM       steps (ProgramData, powercfg, Dism).
REM ============================================================

setlocal
set "PY="

REM ---- interpreter probe chain: py -3 -> python -> known path ----
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

if exist "C:\Python311\python.exe" set "PY=C:\Python311\python.exe"
if defined PY goto HAVE_PY

echo [ERROR] Python not found. Install Python 3.8+ and rerun.
goto END

:HAVE_PY
echo Using interpreter: %PY%
echo.

REM ---- Step 1: read-only report ----
echo ===== Step 1 / Disk report =====
%PY% "%~dp0disk_report.py"
echo.

REM ---- Step 2: dry run (plan only, deletes nothing) ----
echo ===== Step 2 / Dry run: EDIT THE PATHS BELOW FIRST =====
REM %PY% "%~dp0hard_delete.py" "C:\path\to\cache" --dry-run
echo (uncomment the line above and set your paths)
echo.

REM ---- Step 3: real delete (uncomment after reviewing) ----
echo ===== Step 3 / Delete =====
REM %PY% "%~dp0hard_delete.py" "C:\path\to\cache" --log "%~dp0clean.log"
echo (uncomment when you are sure)
echo.

REM ---- Step 4: hibernation off (admin) ----
echo ===== Step 4 / Optional: hibernation off (admin) =====
REM powercfg -h off
echo (skipped by default)

:END
echo.
echo Done.
pause
endlocal
