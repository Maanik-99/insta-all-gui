@echo off
title Installing Required Python Modules
echo ====================================================
echo       Installing Required Modules for Project
echo ====================================================
echo.

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not added to PATH!
    echo Please install Python and check "Add Python to PATH".
    echo.
    pause
    exit /b
)

echo [1/3] Upgrading pip...
python -m pip install --upgrade pip

echo.
echo [2/3] Installing dependencies...
pip install playwright telethon pyotp requests urllib3 gspread google-auth argon2-cffi

echo.
echo [3/3] Installing Playwright browsers...
playwright install chromium

echo.
echo ====================================================
echo       All Modules Installed Successfully!
echo ====================================================
echo.
pause
