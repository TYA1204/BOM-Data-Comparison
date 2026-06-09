@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title BOM对比工具 v1.0

REM ============================================================
REM  BOM Comparison Tool - Startup Script
REM  Usage: double-click to start
REM ============================================================

cd /d "%~dp0"

REM --- Log startup ---
set LOGFILE=%~dp0startup.log
echo [%date% %time%] === BOM Tool Starting === > "%LOGFILE%"

REM ============================================================
REM Step 0: kill any process occupying port 5002
REM ============================================================
echo [INFO] Step 0: Checking port 5002 ...
echo [%time%] Step 0: checking port 5002 >> "%LOGFILE%"

set "FOUND=0"
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":5002 " 2^>nul ^| findstr "LISTENING" 2^>nul') do (
    set "FOUND=1"
    echo [INFO] Killing process PID=%%a on port 5002 --
    echo [%time%] Killing PID %%a >> "%LOGFILE%"
    taskkill /F /PID %%a >> "%LOGFILE%" 2>&1
)
if "!FOUND!"=="1" (
    echo [INFO] Waiting 2 seconds for port release ...
    ping 127.0.0.1 -n 3 >nul 2>&1
) else (
    echo [INFO] Port 5002 is free
)

REM ============================================================
REM Step 1: ensure venv exists
REM ============================================================
echo [INFO] Step 1: Checking Python environment ...
echo [%time%] Step 1: checking venv >> "%LOGFILE%"

if not exist "venv\Scripts\python.exe" (
    echo [SETUP] Creating virtual environment ...
    echo [%time%] Creating venv >> "%LOGFILE%"
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv. Is Python installed?
        echo [%time%] ERROR: venv creation failed >> "%LOGFILE%"
        pause
        exit /b 1
    )
    echo [SETUP] Installing dependencies, please wait ...
    echo [%time%] Installing dependencies >> "%LOGFILE%"
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies
        echo [%time%] ERROR: pip install failed >> "%LOGFILE%"
        pause
        exit /b 1
    )
    echo [OK] Environment setup complete
) else (
    echo [INFO] Python venv found, skipping setup
)

REM ============================================================
REM Step 2: verify Flask can be imported
REM ============================================================
echo [INFO] Step 2: Verifying Flask app ...
echo [%time%] Step 2: verifying app >> "%LOGFILE%"
venv\Scripts\python.exe -c "from app import create_app; create_app(); print('OK')" >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo [ERROR] Failed to import the Flask app. Check startup.log for details.
    echo [%time%] ERROR: app import failed >> "%LOGFILE%"
    pause
    exit /b 1
)
echo [OK] Flask app verified

REM ============================================================
REM Step 3: start Flask server
REM ============================================================
echo.
echo     ==============================================
echo       BOM Comparison Tool v1.0
echo       URL: http://localhost:5002
echo       Press Ctrl+C to stop
echo     ==============================================
echo.
echo [%time%] Starting Flask on port 5002 >> "%LOGFILE%"

REM Start Flask in a separate window
start "BOM-Comparison-Tool" /MIN venv\Scripts\python.exe run.py

REM Wait for Flask to be ready
echo [INFO] Waiting for server to start ...
set "READY=0"
for /L %%i in (1,1,10) do (
    if "!READY!"=="0" (
        ping 127.0.0.1 -n 2 >nul 2>&1
        netstat -ano 2>nul | findstr ":5002 " | findstr "LISTENING" >nul 2>&1
        if not errorlevel 1 set "READY=1"
    )
)

if "!READY!"=="0" (
    echo [ERROR] Server failed to start within 10 seconds
    echo [%time%] ERROR: server startup timeout >> "%LOGFILE%"
    type "%LOGFILE%"
    echo.
    pause
    exit /b 1
)

echo [OK] Server is running on http://localhost:5002

REM ============================================================
REM Step 4: open browser
REM ============================================================
echo [INFO] Opening browser ...
start "" http://localhost:5002

echo.
echo     ==============================================
echo       Server is running. Opening browser now.
echo       The server window will stay in background.
echo       To stop: close the Python window or run
echo       taskkill /F /IM python.exe
echo     ==============================================
echo.
echo [%time%] Browser opened, server running >> "%LOGFILE%"

pause
