@echo off
title AutoMetaInstaBot - Installing Requirements
cd /d "%~dp0"
echo =======================================================
echo  Installing required packages for AutoMetaInstaBot GUI...
echo =======================================================
echo.
pip install -r requirements.txt
echo.
echo Installing Playwright browsers...
playwright install chromium
echo.
echo =======================================================
echo  Installation completed! You can now run run_gui.bat
echo =======================================================
pause
