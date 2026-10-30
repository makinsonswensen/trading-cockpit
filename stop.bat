@echo off
chcp 65001 >nul 2>&1
title 共生者交易决策系统 - 停止

echo ==================================================
echo   停止本项目的后台进程（驾驶舱 + MA 后台）
echo ==================================================
echo.

powershell -NoProfile -Command "$me=$PID; Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -match 'trading-cockpit' -and ($_.CommandLine -match 'app.py' -or $_.CommandLine -match 'scheduler.py') } | ForEach-Object { Write-Host ('  结束 PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo.
echo [OK] 已停止。端口 5001 释放后可再次启动。
echo.
timeout /t 3 /nobreak >nul
