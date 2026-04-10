@echo off
setlocal

set "ROOT=%~dp0"
cd /d "%ROOT%"

echo [watch] Starting Creature in foreground (live logs)...
echo [watch] Close this window or press Ctrl+C to stop foreground session.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%creature_start.ps1" -Foreground

echo.
echo [watch] Foreground session ended.
pause
