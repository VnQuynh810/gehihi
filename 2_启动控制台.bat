@echo off
chcp 65001 >nul
title Google AI Pro (5 TB) Mien Phi 2 Nam - Dieu Khien Tu Dong
cd /d "%~dp0"

echo ======================================================================
echo   Dang khoi dong Google AI Pro (5 TB) Mien Phi 2 Nam...
echo   Dia chi Console: http://127.0.0.1:8765
echo ======================================================================
echo.

python -c "import fastapi, uvicorn, playwright, pydantic" 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [THONG BAO] Dang cai dat thu vien can thiet...
    python -m pip install -r requirements.txt
    python -m playwright install chromium
)

start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8765"
python app.py
pause
