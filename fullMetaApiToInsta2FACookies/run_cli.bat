@echo off
title AutoMetaInstaBot - Full Meta API (CLI Mode)
cd /d "%~dp0"
python autometainstabot.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Application exited with error code %ERRORLEVEL%.
    pause
)
