@echo off
chcp 65001 >nul 2>&1
title 共生者交易决策系统

echo ==================================================
echo   共生者交易决策系统 - 启动脚本
echo ==================================================
echo.
echo   本脚本会启动两个相互独立的进程：
echo     1) MA 共振后台 monitor\scheduler.py
echo        每 5 分钟扫描一次 1h / 15m / 5m 均线共振，结果落盘到 JSON
echo     2) 交易驾驶舱 app.py
echo        只读展示上述 JSON，不参与扫描、不写入状态
echo ==================================================
echo.

cd /d "%~dp0"

:: 检查 .venv（存在则用本地虚拟环境，否则回退到系统 Python）
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] 未找到本地虚拟环境，尝试系统 Python...
    where python >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] 未找到 Python，请先安装 Python 3.9+
        pause
        exit /b 1
    )
    set "PYTHON=python"
    set "PYTHONW=pythonw"
) else (
    set "PYTHON=.venv\Scripts\python.exe"
    set "PYTHONW=.venv\Scripts\pythonw.exe"
)

:: 检查依赖（驾驶舱 + MA 后台）
echo [1/4] 检查依赖...
%PYTHON% -c "import ccxt, flask, flask_cors, numpy, dotenv, pandas, apscheduler, plyer, requests" >nul 2>&1
if errorlevel 1 (
    echo [INFO] 正在安装依赖...
    %PYTHON% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] 依赖安装失败，请手动运行: pip install -r requirements.txt
        pause
        exit /b 1
    )
)
echo       依赖检查通过

:: 检查 .env
echo [2/4] 检查配置文件...
if not exist ".env" (
    if exist ".env.example" (
        echo [INFO] 从 .env.example 创建 .env
        copy .env.example .env >nul
        echo [WARN] 已创建默认 .env，请根据需要修改配置（尤其是 OBSIDIAN_VAULT_PATH）
    )
)
echo       配置文件就绪

:: 先启动 MA 共振后台（独立最小化窗口，不阻塞驾驶舱）
echo [3/4] 启动 MA 共振后台（每 5 分钟扫描，独立窗口）...
start "MA共振后台" /min "%PYTHONW%" "%~dp0monitor\scheduler.py"
echo       已启动，状态文件: monitor\ma_resonance_state.json
rem 稍等后台加载历史状态，避免驾驶舱首个请求读到不存在的文件
timeout /t 2 /nobreak >nul

:: 再前台启动驾驶舱
echo [4/4] 启动交易驾驶舱...
echo.
echo ==================================================
%PYTHON% app.py

pause
