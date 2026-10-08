#!/usr/bin/env bash
# 缠论多级别买点选股 —— 本地一键运行
set -euo pipefail
cd "$(dirname "$0")"

echo "============================================================"
echo " 缠论多级别买点选股 - 本地一键运行"
echo "============================================================"

PY=${PYTHON:-python3}

if [ ! -d ".venv" ]; then
  echo "[1/4] 创建虚拟环境 .venv ..."
  "$PY" -m venv .venv
fi

echo "[2/4] 安装依赖 ..."
./.venv/bin/python -m pip install -q --upgrade pip
./.venv/bin/python -m pip install -q -r requirements.txt

echo "[3/4] 缠论引擎自检 ..."
./.venv/bin/python scripts/selftest.py

echo "[4/4] 执行盘后筛选（全市场，约 8~15 分钟）..."
./.venv/bin/python scripts/screener.py

echo ""
echo "启动本地预览服务器 ..."
exec ./.venv/bin/python serve.py
