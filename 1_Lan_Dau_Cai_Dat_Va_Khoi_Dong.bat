@echo off
chcp 65001 >nul
title Google AI Pro (5 TB) Mien Phi 2 Nam - Cai Dat & Khoi Dong
cd /d "%~dp0"

echo ======================================================================
echo   Dang kiem tra va cai dat thu vien Python (fastapi, uvicorn, playwright)...
echo ======================================================================
python -m pip install -r requirements.txt
python -m playwright install chromium

echo.
echo ======================================================================
echo   Dang khoi dong Web Console (http://127.0.0.1:8765)...
echo ======================================================================
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8765"
python app.py
pause

