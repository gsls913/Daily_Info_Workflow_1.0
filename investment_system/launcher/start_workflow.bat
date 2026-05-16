@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Daily Info Workflow

where py >nul 2>nul
if errorlevel 1 goto use_python
py run_workflow.py %*
goto finished

:use_python
python run_workflow.py %*

:finished
if errorlevel 1 echo.
if errorlevel 1 echo Program exit code: %errorlevel%
if errorlevel 1 echo Recommended command: py "%~dp0run_workflow.py"
pause
