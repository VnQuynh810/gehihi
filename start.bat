@echo off
title Google AI Pro Provisioning Console
cd /d "%~dp0"

REM --- Pick a working Python command (py launcher preferred, then python) ---
set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD (
    python --version >nul 2>&1 && set "PYCMD=python"
)
if not defined PYCMD (
    echo [ERROR] Python 3 was not found on this computer.
    echo         Run setup.bat first, or install Python from
    echo         https://www.python.org/downloads/ (tick "Add python.exe to PATH").
    echo.
    pause
    exit /b 1
)

REM --- Free port 8765 if a previous console instance is still running ---
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /c:"127.0.0.1:8765" ^| findstr "LISTENING"') do (
    echo Closing a previous console instance still using port 8765 (PID %%p)...
    taskkill /f /pid %%p >nul 2>&1
)

echo Starting console at http://127.0.0.1:8765 ...
start "" "http://127.0.0.1:8765"
%PYCMD% app.py

echo.
echo The console has stopped. If there is an error message above, copy it for support.
pause
