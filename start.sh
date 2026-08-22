#!/bin/bash
set -e

echo "=================================================="
echo "  共生者交易决策系统 - 启动脚本"
echo "=================================================="
echo ""

cd "$(dirname "$0")"

# 检查 .venv
if [ -d ".venv" ]; then
    PYTHON=".venv/bin/python"
elif command -v python3 &> /dev/null; then
    PYTHON="python3"
else
    echo "[ERROR] 未找到 Python，请先安装 Python 3.9+"
    exit 1
fi

# 检查依赖
echo "[1/3] 检查依赖..."
$PYTHON -c "import ccxt, flask, flask_cors, numpy" 2>/dev/null || {
    echo "[INFO] 正在安装依赖..."
    $PYTHON -m pip install -r requirements.txt
}
echo "      依赖检查通过"

# 检查 .env
echo "[2/3] 检查配置文件..."
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    echo "[INFO] 从 .env.example 创建 .env"
    cp .env.example .env
    echo "[WARN] 已创建默认 .env，请根据需要修改配置（尤其是 OBSIDIAN_VAULT_PATH）"
fi
echo "      配置文件就绪"

# 启动服务
echo "[3/3] 启动服务..."
echo ""
echo "=================================================="
exec $PYTHON app.py
