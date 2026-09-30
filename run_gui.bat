@echo off
title AutoMetaInstaBot - Multi-Emulator Controller
cd /d "%~dp0\meta_api_gui"
python insta_emulator_gui.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Application exited with error code %ERRORLEVEL%.
    pause
)
