@echo off
cd /d "%~dp0..\.."
py -m investment_system.common.wechat_downloader.wechat_to_md %*
