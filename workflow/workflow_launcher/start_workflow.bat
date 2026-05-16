@echo off
cd /d "%~dp0..\.."
py -m investment_system.launcher.run_workflow %*
