# -*- coding: utf-8 -*-
"""
MA 均线共振后台调度器（从「盯盘查看信号报警/run.py」精简而来，只保留 MA 模块）

职责：每根 5m K线收盘后（第 MA_SCAN_SECOND 秒）执行一次 ma_monitor.scan_all，
结果写入 monitor/ma_resonance_state.json，供驾驶舱只读展示。
不启动形态扫描，不启动网页仪表盘，避免与原项目功能重复。
"""
import os
import sys
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import config
from ma_monitor import get_ma_monitor


def main():
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    print("=" * 60)
    print("MA 均线共振后台 · 每 5 分钟扫描")
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"状态文件: {config.MA_STATE_FILE}")
    print(f"监控标的: {len(config.WATCHLIST)} 个 | 周期 {' + '.join(config.MA_RESONANCE_TIMEFRAMES)}")
    print(f"扫描时点: 每 5 分钟的第 {config.MA_SCAN_SECOND} 秒")
    print("=" * 60)

    monitor = get_ma_monitor()

    scheduler = BackgroundScheduler(timezone='Asia/Shanghai')
    scheduler.add_job(
        monitor.scan_all,
        CronTrigger(minute='*/5', second=config.MA_SCAN_SECOND),
        id='ma_resonance_scan',
        name='MA均线共振扫描',
        misfire_grace_time=120
    )
    scheduler.start()
    print("[调度器] 已启动，等待下一个扫描时点...")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[停止] 正在关闭...")
        scheduler.shutdown()
        print("[停止] 已安全关闭")


if __name__ == '__main__':
    main()
