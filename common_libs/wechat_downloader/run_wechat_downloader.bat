@echo off
chcp 65001 >nul
cd /d "%~dp0common_libs\wechat_downloader"
python wechat_to_md.py %*
pause
