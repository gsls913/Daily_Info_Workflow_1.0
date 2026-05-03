@echo off
chcp 65001 >nul
cd /d "%~dp0"
python get_company_memos.py
pause
