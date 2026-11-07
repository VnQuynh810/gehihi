@echo off
title Google AI Pro Provisioning Console - Setup
cd /d "%~dp0"

echo ============================================================
echo   Google AI Pro Provisioning Console - Setup
echo ============================================================
echo.

REM --- Pick a working Python command (py launcher preferred, then python) ---
set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD (
    python --version >nul 2>&1 && set "PYCMD=python"
)
if not defined PYCMD (
    echo [ERROR] Python 3 was not found on this computer.
    echo.
    echo   Install it from https://www.python.org/downloads/
    echo   and during setup tick "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)
echo Using Python command: %PYCMD%
echo.

echo [1/2] Installing Python dependencies...
%PYCMD% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Installing dependencies failed.
    echo   Check your internet connection, then run setup.bat again.
    echo.
    pause
    exit /b 1
)
echo.

REM --- Free port 8765 if a previous console instance is still running ---
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /c:"127.0.0.1:8765" ^| findstr "LISTENING"') do (
    echo Closing a previous console instance still using port 8765 (PID %%p)...
    taskkill /f /pid %%p >nul 2>&1
)

echo [2/2] Starting console at http://127.0.0.1:8765 ...
start "" "http://127.0.0.1:8765"
%PYCMD% app.py

echo.
echo ============================================================
echo   The console has stopped.
echo   If there is an error message above, copy it for support.
echo ============================================================
pause
