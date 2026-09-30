@echo off
title AutoMetaInstaBot - Full Meta API & Multi-Emulator Dashboard
cd /d "%~dp0"
python insta_emulator_gui.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Application exited with error code %ERRORLEVEL%.
    pause
)
