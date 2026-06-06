@echo off
chcp 65001 >nul
title BOM Data Comparison

echo.
echo ================================
echo     BOM Data Comparison v1.2
echo     http://localhost:5002
echo ================================
echo.

:: Switch to script directory
cd /d "%~dp0"

:: Use system Python (venv is broken)
set PYTHON_EXE=C:\Users\LYP\AppData\Local\Programs\Python\Python312\python.exe

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python 3.12 not found
    pause
    exit /b 1
)

:: Check dependencies
%PYTHON_EXE% -c "import flask; import pandas; import openpyxl; import rapidfuzz" 2>nul
if errorlevel 1 (
    echo [INFO] Installing dependencies...
    %PYTHON_EXE% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed
        pause
        exit /b 1
    )
)

echo [START] Starting Flask server...
echo [URL] http://localhost:5002
echo [STOP] Press Ctrl+C to exit
echo.

%PYTHON_EXE% run.py

pause
