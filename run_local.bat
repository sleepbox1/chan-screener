@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo  缠论多级别买点选股 - 本地一键运行
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 没找到 python，请先安装 Python 3.9+ 并加入 PATH
  pause
  exit /b 1
)

if not exist ".venv" (
  echo [1/4] 创建虚拟环境 .venv ...
  python -m venv .venv
  if errorlevel 1 ( echo [错误] 虚拟环境创建失败 & pause & exit /b 1 )
)

echo [2/4] 安装依赖 ...
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 ( echo [错误] 依赖安装失败 & pause & exit /b 1 )

echo [3/4] 缠论引擎自检 ...
".venv\Scripts\python.exe" scripts\selftest.py
if errorlevel 1 ( echo [错误] 自检未通过，请检查 scripts\chan.py & pause & exit /b 1 )

echo [4/4] 执行盘后筛选（全市场，约 8~15 分钟）...
".venv\Scripts\python.exe" scripts\screener.py

echo.
echo 启动本地预览服务器 ...
".venv\Scripts\python.exe" serve.py
pause
