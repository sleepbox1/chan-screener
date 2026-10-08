@echo off
chcp 65001 >nul
title 缠论买点选股 - 本地网站
cd /d "%~dp0"

REM 优先用现成的运行环境，没有就用系统 python（serve.py 零依赖）
set "PY=C:\Users\cz\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================================
echo  缠论多级别买点选股 · 本地网站启动中...
echo  浏览器将自动打开 http://127.0.0.1:8000
echo  打开后按 Ctrl+D 即可收藏到书签栏
echo  关闭本窗口即停止网站（数据不会丢失）
echo ============================================================
"%PY%" serve.py
pause
