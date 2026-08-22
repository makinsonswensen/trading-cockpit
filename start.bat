@echo off
chcp 65001 >nul 2>&1
title 共生者交易决策系统

echo ==================================================
echo   共生者交易决策系统 - 启动脚本
echo ==================================================
echo.

cd /d "%~dp0"

:: 检查 .venv
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] 未找到本地虚拟环境，尝试系统 Python...
    where python >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] 未找到 Python，请先安装 Python 3.9+
        pause
        exit /b 1
    )
    set "PYTHON=python"
) else (
    set "PYTHON=.venv\Scripts\python.exe"
)

:: 检查依赖
echo [1/3] 检查依赖...
%PYTHON% -c "import ccxt, flask, flask_cors, numpy" >nul 2>&1
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
echo [2/3] 检查配置文件...
if not exist ".env" (
    if exist ".env.example" (
        echo [INFO] 从 .env.example 创建 .env
        copy .env.example .env >nul
        echo [WARN] 已创建默认 .env，请根据需要修改配置（尤其是 OBSIDIAN_VAULT_PATH）
    )
)
echo       配置文件就绪

:: 启动服务
echo [3/3] 启动服务...
echo.
echo ==================================================
%PYTHON% app.py

pause
