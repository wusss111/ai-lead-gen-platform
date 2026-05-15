@echo off
title Customer Platform - Start All

set "ROOT=%~dp0"
set "PYTHONPATH=%ROOT%"

echo ============================================
echo   Customer Platform - Starting All Services
echo ============================================
echo.

echo [1/4] Starting Redis...
start "Redis" "%ROOT%var\redis\redis-server.exe" --port 6379 --loglevel warning
ping -n 3 127.0.0.1 >nul
echo   Redis started (port 6379)
echo.

echo [2/4] Starting RQ Worker - customer_eval...
start "RQ-Eval" cmd /c "set PYTHONPATH=%ROOT% && rq worker -u redis://127.0.0.1:6379/0 customer_eval:default"
ping -n 1 127.0.0.1 >nul
echo   Eval worker started
echo.

echo [3/4] Starting RQ Worker - inquiry_mail...
start "RQ-Mail" cmd /c "set PYTHONPATH=%ROOT% && rq worker -u redis://127.0.0.1:6379/0 inquiry_mail:default"
start "RQ-Mail-Send" cmd /c "set PYTHONPATH=%ROOT% && rq worker -u redis://127.0.0.1:6379/0 inquiry_mail:send"
ping -n 1 127.0.0.1 >nul
echo   Mail workers started
echo.

echo [4/4] Starting Web Server...
start "Web" cmd /c "set PYTHONPATH=%ROOT% && python -m uvicorn src.core.app:app --host 127.0.0.1 --port 8000"
ping -n 5 127.0.0.1 >nul
echo   Web started (http://127.0.0.1:8000)
echo.

echo Opening browser...
start http://127.0.0.1:8000

echo.
echo ============================================
echo   All services started!
echo.
echo   Web     : http://127.0.0.1:8000
echo   Modules : Customer Eval / CRM / Inquiry Mail
echo.
echo   Close individual windows to stop services.
echo ============================================
pause
