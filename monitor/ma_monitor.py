# -*- coding: utf-8 -*-
"""
MA 均线多周期共振监控器

职责：
1. 每根 5m K线收盘后，拉取 1h / 15m / 5m 三个周期的K线
2. 判定各周期等级并做三周期共振判断
3. 仅在共振状态发生跳变时推送（建立多头/空头共振、共振解除）
4. 状态持久化，进程重启后不会重复报警

判定逻辑全部复用 ma_resonance 模块，本模块只负责取数、状态与推送。
"""
import json
import os
import time
from datetime import datetime

import config
from data_fetcher import get_data_fetcher
from ma_resonance import (
    RESONANCE_BEAR,
    RESONANCE_BULL,
    RESONANCE_RELEASE,
    ResonanceStateTracker,
    build_alert_message,
    classify_state,
    drop_unclosed_candles,
    format_letters,
    judge_resonance,
    latest_closed_candle_ms,
    timeframe_to_ms,
)
from notifier import send_desktop_notification, send_feishu_message


class MAResonanceMonitor:
    """MA 多周期共振监控器"""

    def __init__(self):
        self.fetcher = get_data_fetcher()
        # {symbol: 状态快照 dict}，含候选状态，用于进程重启后继续确认过程
        self.snapshots = {}
        # {symbol: ResonanceStateTracker}
        self.trackers = {}
        # {symbol: 'ok' | 'skipped' | 'unavailable'}，本次扫描的取数/判定结果，
        # 用于区分“当前无信号”和“取数失败只能沿用旧快照”
        self.scan_status = {}
        self.history = []
        self.scan_count = 0
        self.total_alerts = 0
        self.last_scan_time = None
        self._load_state()

    # ==================== 状态持久化 ====================

    def _load_state(self):
        """恢复上次的状态快照，避免进程重启后重复报警、并延续未完成的确认过程"""
        try:
            if not os.path.exists(config.MA_STATE_FILE):
                return
            with open(config.MA_STATE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.snapshots = data.get('snapshots', {})
            self.history = data.get('history', [])
            self.total_alerts = data.get('total_alerts', 0)
            self.scan_count = data.get('scan_count', 0)
            print(f"[MA共振] 已恢复 {len(self.snapshots)} 个标的的共振状态"
                  f"（历史事件 {len(self.history)} 条）")
        except Exception as e:
            print(f"[MA共振] 状态恢复失败，按空状态启动: {e}")
            self.snapshots = {}
            self.history = []

    def _save_state(self):
        try:
            if len(self.history) > config.MA_MAX_HISTORY:
                self.history = self.history[-config.MA_MAX_HISTORY:]

            # detail 体积较大且只用于当次文案，不写入磁盘
            history = [
                {k: v for k, v in event.items() if k != 'detail'}
                for event in self.history
            ]
            payload = {
                'snapshots': self.snapshots,
                'history': history,
                'total_alerts': self.total_alerts,
                'scan_count': self.scan_count,
                'updated_at': datetime.now().isoformat(),
            }
            with open(config.MA_STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            print(f"[MA共振] 状态保存失败: {e}")

    # ==================== 判定 ====================

    def _fetch_period_state(self, symbol, timeframe, now_ms):
        """拉取单个周期数据并分级，数据不可用返回 None"""
        df = self.fetcher.fetch_ohlcv(symbol, timeframe, config.MA_KLIMIT)
        if df is None or df.empty:
            return None
        df = drop_unclosed_candles(df, timeframe, now_ms)
        if df.empty:
            return None
        return classify_state(df)

    def eval_symbol(self, symbol, now_ms=None):
        """
        判定某标的三个周期的状态。

        Returns:
            (states, details) 或 None（任一周期数据不可用，本次不做判定）
        """
        if now_ms is None:
            now_ms = int(time.time() * 1000)

        states = {}
        details = {}
        for timeframe in config.MA_RESONANCE_TIMEFRAMES:
            info = self._fetch_period_state(symbol, timeframe, now_ms)
            if info is None:
                print(f"[MA共振] {symbol} {timeframe} 数据不可用，跳过本次判定")
                return None
            states[timeframe] = info['state']
            details[timeframe] = info
        return states, details

    def _get_tracker(self, symbol):
        """
        取该标的的状态机，首次访问时从已落盘快照恢复，延续未完成的确认过程。
        """
        tracker = self.trackers.get(symbol)
        if tracker is None:
            snapshot = self.snapshots.get(symbol, {})
            tracker = ResonanceStateTracker(
                confirm_scans=config.MA_CONFIRM_SCANS,
                release_scans=config.MA_RELEASE_CONFIRM_SCANS,
                formal=snapshot.get('kind'),
                candidate=snapshot.get('candidate', snapshot.get('kind')),
                candidate_count=snapshot.get('candidate_count', 0),
                last_candle_ms=snapshot.get('last_candle_ms'),
                candle_step_ms=timeframe_to_ms(config.MA_RESONANCE_TIMEFRAMES[-1]),
            )
            self.trackers[symbol] = tracker
        return tracker

    def _make_snapshot(self, symbol, tracker, states, summary):
        return {
            'symbol': symbol,
            'kind': tracker.formal,
            'candidate': tracker.candidate,
            'candidate_count': tracker.candidate_count,
            'last_candle_ms': tracker.last_candle_ms,
            'summary': summary,
            'states': states,
            'updated_at': datetime.now().isoformat(),
        }

    def scan_symbol(self, symbol, now_ms=None):
        """
        扫描单个标的，返回本次正式状态切换事件；无切换或数据不可用返回 None。

        状态机说明：候选共振状态必须连续 config.MA_CONFIRM_SCANS 根 5m K线判定一致
        才正式切换，单根 K线的边界抖动会被吸收，不产生通知。

        计数以 K线为粒度：同一根 5m K线被重复扫描（手动重跑、调度 misfire）
        只算一次，不会虚高确认进度。
        """
        candle_ms = latest_closed_candle_ms(config.MA_RESONANCE_TIMEFRAMES[-1], now_ms)
        prev = self.snapshots.get(symbol, {})
        if prev.get('last_candle_ms') is not None and candle_ms <= prev['last_candle_ms']:
            # 同一根（或更早的）K线已经判定过，数据未变，直接跳过
            self.scan_status[symbol] = 'skipped'
            return None

        result = self.eval_symbol(symbol, now_ms)
        if result is None:
            # 取数失败：保留旧快照以延续确认进度，但本次结果标记为不可用，
            # 展示时不能把旧结论当成当前结论
            self.scan_status[symbol] = 'unavailable'
            return None
        self.scan_status[symbol] = 'ok'

        states, details = result
        kind = judge_resonance(states)

        tracker = self._get_tracker(symbol)
        change = tracker.update(kind, candle_ms)

        # summary 只在建立共振时刷新；共振持续期间保留建立时的字母，
        # 这样解除通知里的“上次共振”才是建立共振那一刻的信号组合。
        if change is not None and change[1] is not None:
            summary = format_letters(states)
        elif tracker.formal is not None:
            summary = prev.get('summary') or format_letters(states)
        else:
            summary = format_letters(states)

        self.snapshots[symbol] = self._make_snapshot(symbol, tracker, states, summary)

        if change is None:
            return None

        prev_kind, new_kind = change
        event_kind = RESONANCE_RELEASE if new_kind is None else new_kind

        price_timeframe = config.MA_RESONANCE_TIMEFRAMES[-1]
        event = {
            'symbol': symbol,
            'kind': event_kind,
            'prev_kind': prev_kind,
            'prev_summary': prev.get('summary'),
            'states': states,
            'summary': summary,
            'price': details[price_timeframe]['close'],
            'detail': details,
            'detected_at': datetime.now().isoformat(),
        }
        self.history.append(event)
        self.total_alerts += 1
        self._save_state()
        return event

    # ==================== 供其他入场信号调用的过滤器接口 ====================

    def get_trend(self, symbol):
        """
        查询某标的的趋势状态（读本地快照，不取数、不触发推送）。

        Returns:
            dict 或 None：
                direction —— 已确认方向 'bull'/'bear'/None（需连续 N 根确认）
                current   —— 当前 K线的瞬时字母组合，仅用于展示
                updated_at
        """
        snapshot = self.snapshots.get(symbol)
        if not snapshot:
            return None
        return {
            'symbol': symbol,
            'direction': snapshot.get('kind'),
            'current': snapshot.get('summary'),
            'updated_at': snapshot.get('updated_at'),
        }

    def allow_entry(self, symbol, side):
        """
        入场方向过滤：只有已确认趋势方向与开仓方向一致时才放行。

        这是本模块的定位——不单独作为开仓信号，而是给其他入场信号做方向闸门。

        Args:
            symbol: 交易对
            side: 'long' 或 'short'

        Returns:
            (bool, str): 是否放行, 原因说明
        """
        trend = self.get_trend(symbol)
        if trend is None:
            return False, '无趋势快照'
        need = 'bull' if side == 'long' else 'bear'
        if trend['direction'] == need:
            return True, f"顺向（{trend['current']}）"
        if trend['direction'] is None:
            return False, f"趋势未确认（当前 {trend['current'] or '非共振'}）"
        return False, f"反向（当前 {trend['current']}，趋势为{'多头' if trend['direction'] == 'bull' else '空头'}）"

    # ==================== 扫描与推送 ====================

    def scan_all(self):
        """扫描所有监控标的，检测共振跳变并推送"""
        now_ms = int(time.time() * 1000)
        self.last_scan_time = datetime.now()
        self.scan_count += 1

        print(f"\n{'=' * 60}")
        print(f"[MA共振扫描] {self.last_scan_time.strftime('%Y-%m-%d %H:%M:%S')}"
              f" | 标的 {len(config.WATCHLIST)} 个 | 周期 {'/'.join(config.MA_RESONANCE_TIMEFRAMES)}")
        print(f"{'=' * 60}")

        events = []
        for i, symbol in enumerate(config.WATCHLIST):
            print(f"[{i + 1}/{len(config.WATCHLIST)}] {symbol} ...", end=' ')
            try:
                event = self.scan_symbol(symbol, now_ms)
            except Exception as e:
                print(f"判定异常: {e}")
                continue

            if event:
                events.append(event)
                print(f"共振跳变 -> {event['kind']} ({event['summary']})")
                self._notify(event)
            else:
                status = self.scan_status.get(symbol)
                snapshot = self.snapshots.get(symbol, {})
                if status == 'unavailable':
                    print("数据不可用，本次跳过判定")
                    continue

                current = snapshot.get('kind') or '非共振'
                pending = snapshot.get('candidate')
                count = snapshot.get('candidate_count', 0)
                extra = ''
                if count and pending != snapshot.get('kind'):
                    # 解除（候选为 None）走慢速阈值，展示时用对应分母
                    required = (config.MA_RELEASE_CONFIRM_SCANS if pending is None
                                else config.MA_CONFIRM_SCANS)
                    extra = f"，候选 {pending or '非共振'} ({count}/{required})"
                prefix = '同根K线已判定' if status == 'skipped' else '无跳变'
                print(f"{prefix} (当前 {current} / {snapshot.get('summary', '-')}{extra})")

        print(f"\n[MA共振扫描完成] 本次跳变 {len(events)} 个，累计报警 {self.total_alerts} 次")

        # 扫描计数也需要落盘（跳变时已落盘，这里补记普通扫描）
        if not events:
            self._save_state()

        return {
            'scan_time': self.last_scan_time.isoformat(),
            'total_scanned': len(config.WATCHLIST),
            'events': events,
        }

    def _notify(self, event):
        """发送飞书 + 桌面通知"""
        title, content, template = build_alert_message(
            symbol=event['symbol'],
            kind=event['kind'],
            states=event['states'],
            details=event['detail'],
            prev_kind=event.get('prev_kind'),
            prev_summary=event.get('prev_summary'),
            price=event.get('price'),
        )

        webhook = config.MA_FEISHU_WEBHOOK_URL or None
        if config.ENABLE_FEISHU:
            send_feishu_message(
                title, content, is_markdown=True,
                template=template, webhook_url=webhook,
            )

        if config.ENABLE_DESKTOP_NOTIFY and config.MA_ENABLE_DESKTOP_NOTIFY:
            plain = content.replace('**', '').replace(' ｜ ', ' ')
            send_desktop_notification(title, plain)


# 全局单例
_monitor = None


def get_ma_monitor():
    global _monitor
    if _monitor is None:
        _monitor = MAResonanceMonitor()
    return _monitor


if __name__ == '__main__':
    """
    命令行入口：用真实行情跑一次完整扫描。

    用法：
        python ma_monitor.py               # 真实扫描（读写正式状态文件、正常推送）
        python ma_monitor.py --dry-run     # 真实行情但不推送、不碰正式状态文件
    """
    import argparse
    import tempfile

    parser = argparse.ArgumentParser(description='MA 均线共振 - 手动执行一次扫描')
    parser.add_argument('--dry-run', action='store_true',
                        help='只取数判定，不推送、不写正式状态文件')
    args = parser.parse_args()

    if args.dry_run:
        config.ENABLE_FEISHU = False
        config.ENABLE_DESKTOP_NOTIFY = False
        config.MA_ENABLE_DESKTOP_NOTIFY = False
        config.MA_STATE_FILE = os.path.join(
            tempfile.mkdtemp(prefix='ma_dryrun_'), 'state.json'
        )
        print("[dry-run] 关闭推送，状态写入临时文件，不影响正式状态")

    monitor = MAResonanceMonitor()
    result = monitor.scan_all()

    print()
    for symbol in config.WATCHLIST:
        status = monitor.scan_status.get(symbol)
        if status == 'unavailable':
            print(f"  {symbol:<14} 数据不可用")
            continue
        snapshot = monitor.snapshots.get(symbol)
        if not snapshot:
            print(f"  {symbol:<14} 本次无判定")
            continue
        kind = snapshot.get('kind') or '非共振'
        pending = snapshot.get('candidate')
        count = snapshot.get('candidate_count', 0)
        extra = ''
        if count and pending != snapshot.get('kind'):
            required = (config.MA_RELEASE_CONFIRM_SCANS if pending is None
                        else config.MA_CONFIRM_SCANS)
            extra = f" | 候选 {pending or '非共振'} {count}/{required}"
        mark = ' [同根]' if status == 'skipped' else ''
        print(f"  {symbol:<14} {kind:<8} {snapshot.get('summary', '-')}{extra}{mark}")

    print()
    print(f"扫描完成：{result['total_scanned']} 个标的，跳变 {len(result['events'])} 个")
