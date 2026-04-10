@echo off
setlocal

set "ROOT=%~dp0"
cd /d "%ROOT%"

if not exist "%ROOT%journal_data" mkdir "%ROOT%journal_data"

echo [sleep] Stopping Creature...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%creature_stop.ps1" > "%ROOT%journal_data\sleep_status.txt" 2>&1

echo [sleep] Done.
exit /b 0
