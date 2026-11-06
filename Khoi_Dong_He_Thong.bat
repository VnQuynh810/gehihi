@echo off
chcp 65001 >nul
title Google AI Pro (5 TB) 2 Năm Miễn Phí - Điều Khiển Tự Động
cd /d "%~dp0"

echo ======================================================================
echo   Đang kiểm tra và khởi động Google AI Pro (5 TB) 2 Năm...
echo   Địa chỉ Web Console: http://127.0.0.1:8765
echo ======================================================================
echo.

:: Tự động kiểm tra và cài đặt thư viện nếu máy còn thiếu
python -c "import fastapi, uvicorn, playwright, pydantic" 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [THÔNG BÁO] Phát hiện máy chưa cài đủ thư viện, đang tự động cài đặt...
    python -m pip install -r requirements.txt
    python -m playwright install chromium
    echo.
)

:: Mở trình duyệt sau 2 giây khi server FastAPI đã bắt đầu lắng nghe cổng 8765
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8765"

:: Chạy server chính
python app.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ======================================================================
    echo   [LỖI] Không thể khởi động máy chủ app.py!
    echo   Vui lòng thử chạy file: Cai_Dat_Moi_Truong.bat để kiểm tra môi trường.
    echo ======================================================================
    pause
)
pause
