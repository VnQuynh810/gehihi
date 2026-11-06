@echo off
chcp 65001 >nul
title Google AI Pro (5 TB) - Cài Đặt Thư Viện & Khởi Động
cd /d "%~dp0"

echo ======================================================================
echo   BƯỚC 1: Đang kiểm tra và cài đặt các thư viện Python cần thiết...
echo   (fastapi, uvicorn, playwright, pydantic)
echo ======================================================================
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [CẢNH BÁO] Cài đặt qua nguồn mặc định gặp trục trặc, đang thử qua nguồn quốc tế...
    python -m pip install fastapi uvicorn playwright pydantic
)

echo.
echo ======================================================================
echo   BƯỚC 2: Cài đặt Chromium cho Playwright (nếu chưa có)...
echo ======================================================================
python -m playwright install chromium

echo.
echo ======================================================================
echo   BƯỚC 3: Đang khởi động bảng điều khiển Web UI: http://127.0.0.1:8765
echo ======================================================================
start "" "http://127.0.0.1:8765"
python app.py
pause

