@echo off
chcp 65001 >nul
title AlphaPai Meeting Minutes Download Tool
cd /d "%~dp0"

echo Starting AlphaPai download script...
echo Current directory: %cd%
echo.

REM Check if python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python or add it to your PATH
    pause
    exit /b 1
)

echo Python found, running script...
echo.

python alphapai_download.py --auto

echo.
echo Script finished with exit code: %errorlevel%
pause
