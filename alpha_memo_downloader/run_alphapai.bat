@echo off
cd /d "%~dp0.."
py -m investment_system.collectors.alpha_memo.alphapai_download %*
