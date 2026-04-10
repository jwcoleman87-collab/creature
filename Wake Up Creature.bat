@echo off
setlocal

set "ROOT=%~dp0"
cd /d "%ROOT%"

if not exist "%ROOT%journal_data" mkdir "%ROOT%journal_data"

echo [wake] Starting Creature...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%creature_start.ps1" > "%ROOT%journal_data\wake_up.log" 2>&1

timeout /t 3 /nobreak >nul

echo [wake] Opening dashboard...
rundll32 url.dll,FileProtocolHandler http://127.0.0.1:8080 >nul 2>&1

echo [wake] Capturing status...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%creature_status.ps1" > "%ROOT%journal_data\wake_status.txt" 2>&1

echo [wake] Done.
exit /b 0
