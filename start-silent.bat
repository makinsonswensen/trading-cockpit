@echo off
chcp 65001 >nul 2>&1
title 共生者交易决策系统 - 后台静默启动

cd /d "%~dp0"

echo ==================================================
echo   后台静默启动（无窗口，误关窗口不影响运行）
echo ==================================================
echo.

:: 端口占用检查：Windows 允许多个进程同时 bind 同一端口
netstat -ano | findstr ":5001" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [ERROR] 端口 5001 已被占用，驾驶舱可能已在运行。
    echo         如需重启，请先运行 stop.bat
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-silent.ps1"

echo.
echo [OK] 已在后台启动：MA 共振后台 + 交易驾驶舱
echo      日志: monitor\monitor.log  cockpit.log
echo      停止: 运行 stop.bat
echo.
timeout /t 3 /nobreak >nul
