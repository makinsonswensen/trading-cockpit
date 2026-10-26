#!/bin/bash
set -e

echo "=================================================="
echo "  共生者交易决策系统 - 启动脚本"
echo "=================================================="
echo ""
echo "  本脚本会启动两个相互独立的进程："
echo "    1) MA 共振后台 monitor/scheduler.py"
echo "       每 5 分钟扫描一次 1h / 15m / 5m 均线共振，结果落盘到 JSON"
echo "    2) 交易驾驶舱 app.py"
echo "       只读展示上述 JSON，不参与扫描、不写入状态"
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

# 检查依赖（驾驶舱 + MA 后台）
echo "[1/4] 检查依赖..."
$PYTHON -c "import ccxt, flask, flask_cors, numpy, dotenv, pandas, apscheduler, plyer, requests" 2>/dev/null || {
    echo "[INFO] 正在安装依赖..."
    $PYTHON -m pip install -r requirements.txt
}
echo "      依赖检查通过"

# 检查 .env
echo "[2/4] 检查配置文件..."
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    echo "[INFO] 从 .env.example 创建 .env"
    cp .env.example .env
    echo "[WARN] 已创建默认 .env，请根据需要修改配置（尤其是 OBSIDIAN_VAULT_PATH）"
fi
echo "      配置文件就绪"

# 先启动 MA 共振后台（后台进程，日志写入 monitor.log）
echo "[3/4] 启动 MA 共振后台（每 5 分钟扫描）..."
nohup $PYTHON monitor/scheduler.py > monitor.log 2>&1 &
MA_PID=$!
echo "      已启动，PID=$MA_PID，日志: monitor.log"
echo "      状态文件: monitor/ma_resonance_state.json"
sleep 1

# 再前台启动驾驶舱
echo "[4/4] 启动交易驾驶舱..."
echo ""
echo "=================================================="
$PYTHON app.py

echo ""
echo "[提示] 驾驶舱已退出，MA 后台仍在后台运行"
echo "       如需停止 MA 后台: kill $MA_PID"
echo "       或统一清理: pkill -f monitor/scheduler.py"
