@echo off
title Creature — Setup
color 0A
echo.
echo  ==========================================
echo   CREATURE — Phase 0 Setup
echo  ==========================================
echo.

:: Check Python exists
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: Python not found.
    echo  Make sure Python is installed and try again.
    pause
    exit /b 1
)

echo  Python found.
echo.

:: Create virtual environment
echo  Creating virtual environment (venv)...
python -m venv venv
if %errorlevel% neq 0 (
    echo  ERROR: Failed to create virtual environment.
    pause
    exit /b 1
)
echo  Virtual environment created.
echo.

:: Install dependencies
echo  Installing dependencies from requirements.txt...
venv\Scripts\pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo  ERROR: Failed to install dependencies.
    pause
    exit /b 1
)
echo.
echo  Dependencies installed.
echo.
echo  ==========================================
echo   Setup complete!
echo.
echo   NEXT STEPS:
echo   1. Open your .env file in Notepad
echo   2. Paste in your Alpaca API key and secret
echo   3. Run: run_phase0.bat
echo  ==========================================
echo.
pause
