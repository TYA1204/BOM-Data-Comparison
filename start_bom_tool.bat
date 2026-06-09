@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

REM --- Log all output ---
set LOG=startup_debug.log
echo ========== %date% %time% ========== > "%LOG%"
echo Working dir: %CD% >> "%LOG%"
echo. >> "%LOG%"

REM --- Check venv python ---
echo [1] Checking venv python... >> "%LOG%"
if not exist "venv\Scripts\python.exe" (
    echo [ERROR] venv\Scripts\python.exe not found! >> "%LOG%"
    echo [ERROR] Please run: python -m venv venv >> "%LOG%"
    goto SHOW_LOG
)
echo [OK] venv python found. >> "%LOG%"

REM --- Test import flask ---
echo [2] Testing flask import... >> "%LOG%"
venv\Scripts\python.exe -c "import flask; print('Flask', flask.__version__)" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] Flask import failed. >> "%LOG%"
    goto SHOW_LOG
)
echo [OK] Flask import OK. >> "%LOG%"

REM --- Check run.py exists ---
echo [3] Checking run.py... >> "%LOG%"
if not exist "run.py" (
    echo [ERROR] run.py not found in %CD% >> "%LOG%"
    goto SHOW_LOG
)
echo [OK] run.py found. >> "%LOG%"

REM --- Start Flask, capture output ---
echo [4] Starting Flask... >> "%LOG%"
echo Flask output follows: >> "%LOG%"
echo ================================== >> "%LOG%"

venv\Scripts\python.exe run.py >> "%LOG%" 2>&1

echo ================================== >> "%LOG%"
echo Flask exited with code %ERRORLEVEL%. >> "%LOG%"

:SHOW_LOG
echo.
echo ========== STARTUP LOG ==========
type "%LOG%"
echo ========== END OF LOG ==========
echo.
pause
