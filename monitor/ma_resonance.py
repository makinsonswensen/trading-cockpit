# -*- coding: utf-8 -*-
"""
MA 均线多周期共振判定模块

判定口径（P = 已收盘K线收盘价，A = MA7，B = MA25，C = MA50）：
    A>B 且 P>A 且 B>C    -> L1 一级多头（1h=AA / 15m=BB / 5m=CC）
    A>B 且（P<A 或 B<C）  -> L2 二级多头（1h=A  / 15m=B  / 5m=C）
    A<=B 且 P<A          -> S  空头
    A<=B 且 P>=A         -> 中性（不参与共振）

共振：三周期全部为多头 -> bull；三周期全部为空头 -> bear；其余 -> None

本模块只做纯计算与文案生成，不取数、不推送，便于实盘与回测共用同一份判定逻辑。
"""
import time
from datetime import datetime

import pandas as pd

import config

# ==================== 状态常量 ====================
STATE_LEVEL1 = 'L1'    # 一级多头
STATE_LEVEL2 = 'L2'    # 二级多头
STATE_SHORT = 'S'      # 空头
STATE_NEUTRAL = None   # 中性（MA7 在 MA25 下方，但价格仍在 MA7 上方）

# 共振方向
RESONANCE_BULL = 'bull'
RESONANCE_BEAR = 'bear'
RESONANCE_RELEASE = 'release'

# 周期毫秒表
_TIMEFRAME_UNITS = {
    'm': 60 * 1000,
    'h': 60 * 60 * 1000,
    'd': 24 * 60 * 60 * 1000,
    'w': 7 * 24 * 60 * 60 * 1000,
}


def timeframe_to_ms(timeframe):
    """把 ccxt 周期字符串转成毫秒，如 '5m' -> 300000"""
    text = str(timeframe).strip().lower()
    for suffix, unit_ms in _TIMEFRAME_UNITS.items():
        if text.endswith(suffix) and text[:-1].isdigit():
            return int(text[:-1]) * unit_ms
    raise ValueError(f'无法识别的K线周期: {timeframe}')


def index_to_ms(index):
    """
    把 DatetimeIndex 转成毫秒时间戳。

    先强制转成 datetime64[ns] 再取整，避免依赖 pandas 内部存储精度
    （pandas 3.0 起按输入精度存储，可能是 ms，直接 astype('int64') 会得到毫秒而非纳秒）。
    """
    return index.astype('datetime64[ns]').astype('int64') // 1_000_000


def drop_unclosed_candles(df, timeframe, now_ms=None):
    """
    只保留已经收盘的K线。

    ccxt 返回的 timestamp 是K线开盘时间，收盘时间 = 开盘时间 + 周期时长。
    丢弃收盘时间晚于当前时间的K线，避免用到未走完的K线导致信号闪烁。
    """
    if df is None or df.empty:
        return df
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    tf_ms = timeframe_to_ms(timeframe)
    close_ms = index_to_ms(df.index) + tf_ms
    return df[close_ms <= now_ms]


def latest_closed_candle_ms(timeframe, now_ms=None):
    """
    返回最近一根已收盘K线的开盘时间戳（毫秒），作为该根K线的唯一标识。

    口径与 drop_unclosed_candles 一致：收盘时间 <= 当前时间的最后一根。
    用于让状态机按K线粒度计数，而不是按扫描次数计数。
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    tf_ms = timeframe_to_ms(timeframe)
    return (now_ms // tf_ms) * tf_ms - tf_ms


def calc_ma_values(df, periods=None):
    """
    计算最后一根K线对应的收盘价与三根均线值。

    Returns:
        (close, ma_fast, ma_mid, ma_slow) 或 None（数据不足 / 含空值）
    """
    if periods is None:
        periods = config.MA_PERIODS
    fast, mid, slow = periods
    if df is None or len(df) < slow:
        return None

    close_series = df['close']
    values = [close_series.rolling(p).mean().iloc[-1] for p in (fast, mid, slow)]
    if any(pd.isna(v) for v in values):
        return None
    return (
        float(close_series.iloc[-1]),
        float(values[0]),
        float(values[1]),
        float(values[2]),
    )


def classify_values(close, ma_fast, ma_mid, ma_slow):
    """
    核心判定：由价格与三根均线值给出等级。

    回测与实盘共用这一份逻辑，避免两套代码判定不一致。
    任一取值为空则返回 None（表示无法判定）。
    """
    if any(pd.isna(v) for v in (close, ma_fast, ma_mid, ma_slow)):
        return None

    if ma_fast > ma_mid:
        # MA7 在 MA25 上方 = 短期多头排列
        if close > ma_fast and ma_mid > ma_slow:
            return STATE_LEVEL1
        return STATE_LEVEL2

    # MA7 不在 MA25 上方：需要价格也跌破 MA7 才算空头，否则视为中性
    if close < ma_fast:
        return STATE_SHORT
    return STATE_NEUTRAL


def classify_state(df, periods=None):
    """
    单周期分级。

    Returns:
        dict: {'state': 'L1'|'L2'|'S'|None, 'close': float, 'ma_fast': float,
               'ma_mid': float, 'ma_slow': float}
        None: 数据不足，无法判定（与“中性”区分：中性是 state=None 的 dict）
    """
    values = calc_ma_values(df, periods)
    if values is None:
        return None
    close, ma_fast, ma_mid, ma_slow = values
    return {
        'state': classify_values(close, ma_fast, ma_mid, ma_slow),
        'close': close,
        'ma_fast': ma_fast,
        'ma_mid': ma_mid,
        'ma_slow': ma_slow,
    }


def judge_resonance(states):
    """
    三周期共振判定。

    Args:
        states: {周期: 状态}，状态取值 'L1' / 'L2' / 'S' / None

    Returns:
        'bull' / 'bear' / None
    """
    values = [states.get(tf) for tf in config.MA_RESONANCE_TIMEFRAMES]
    if any(v is None for v in values):
        return None
    if all(v in (STATE_LEVEL1, STATE_LEVEL2) for v in values):
        return RESONANCE_BULL
    if all(v == STATE_SHORT for v in values):
        return RESONANCE_BEAR
    return None


_UNSET = object()


class ResonanceStateTracker:
    """
    共振状态机：候选状态必须连续 confirm_scans 根 K线判定一致，才正式切换。

    用于抑制 5m 周期价格在 MA7 附近来回穿越造成的“建立 -> 解除 -> 重建”抖动。
    confirm_scans=4 时，需连续 4 根 5m K线维持同一状态才产生通知。

    计数以 K线为粒度：同一根 K线被重复判定（手动重跑、调度延迟重入、misfire）
    只计一次，不会虚高确认次数。传入 candle_step_ms 后还会校验相邻性：
    中间缺失K线（进程停机、取数失败）视为连续性中断，重新开始计数。

    进出场不对称：建立共振用 confirm_scans 根确认（快），解除共振用
    release_scans 根确认（慢）。趋势中的回踩只是 5m 短暂回到 MA7 上方，
    1h/15m 趋势并未改变，不应因此报“共振解除”。
    """

    def __init__(self, confirm_scans=2, formal=None, candidate=_UNSET,
                 candidate_count=0, last_candle_ms=None, candle_step_ms=None,
                 release_scans=None):
        self.confirm_scans = max(1, int(confirm_scans))
        # 未指定时与进场一致，等价于对称确认（原始行为）
        self.release_scans = max(
            1, int(release_scans if release_scans is not None else self.confirm_scans)
        )
        self.formal = formal
        # 未指定候选时，默认为“无待确认变化”
        self.candidate = formal if candidate is _UNSET else candidate
        self.candidate_count = int(candidate_count)
        self.last_candle_ms = last_candle_ms
        self.candle_step_ms = candle_step_ms

    def _required_count(self, kind):
        """目标状态所需连续确认根数：解除（None）走慢速阈值"""
        return self.release_scans if kind is None else self.confirm_scans

    def update(self, kind, candle_ms=None):
        """
        喂入一次判定结果。

        Args:
            kind: 本次判定结果（'bull' / 'bear' / None）
            candle_ms: 该结果对应的 K线标识（通常是已收盘 K线的开盘时间戳，毫秒）。
                传入时按 K线粒度去重：同一根或更早的 K线不会重复推进确认计数。

        Returns:
            (prev_kind, new_kind): 正式状态发生切换
            None: 未切换（含抖动被吸收、同一根 K线重复判定的情况）
        """
        if candle_ms is not None:
            if self.last_candle_ms is not None:
                if candle_ms <= self.last_candle_ms:
                    # 同一根或更早的K线，数据未变，不计入
                    return None
                if (self.candle_step_ms
                        and candle_ms - self.last_candle_ms > self.candle_step_ms):
                    # 中间缺失K线，确认过程不再连续，重新开始计数
                    self.candidate = self.formal
                    self.candidate_count = 0
            self.last_candle_ms = candle_ms

        if kind == self.formal:
            # 与已确认状态一致，撤销正在观察的候选
            self.candidate = self.formal
            self.candidate_count = 0
            return None

        if kind == self.candidate:
            self.candidate_count += 1
        else:
            self.candidate = kind
            self.candidate_count = 1

        if self.candidate_count >= self._required_count(self.candidate):
            prev = self.formal
            self.formal = kind
            self.candidate = kind
            self.candidate_count = 0
            return prev, kind

        return None

    def to_dict(self):
        return {
            'kind': self.formal,
            'candidate': self.candidate,
            'candidate_count': self.candidate_count,
            'confirm_scans': self.confirm_scans,
            'release_scans': self.release_scans,
            'last_candle_ms': self.last_candle_ms,
            'candle_step_ms': self.candle_step_ms,
        }


def describe_state(timeframe, state):
    """周期的可读描述：多头显示字母，空头只显示“空头”，中性显示“中性”"""
    if state is None:
        return '中性'
    if state == STATE_SHORT:
        return '空头'
    letters = config.MA_GRADE_LETTERS.get(timeframe, {})
    return letters.get(state, state)


def format_letters(states):
    """合并各周期字母，如 'AA+BB+CC'（空头/中性会显示为对应中文）"""
    return '+'.join(
        describe_state(tf, states.get(tf)) for tf in config.MA_RESONANCE_TIMEFRAMES
    )


def _fmt_price(value):
    """按数量级选择合适的小数位"""
    if value >= 1000:
        return f'{value:,.2f}'
    if value >= 1:
        return f'{value:.4f}'
    return f'{value:.6f}'


def _format_period_line(timeframe, info):
    label = config.MA_TIMEFRAME_LABELS.get(timeframe, timeframe)
    if info is None:
        return f'**{label}**: 数据不足'
    return (
        f"**{label}**: {describe_state(timeframe, info['state'])}"
        f" ｜ 价 {_fmt_price(info['close'])}"
        f" ｜ MA7 {_fmt_price(info['ma_fast'])}"
        f" ｜ MA25 {_fmt_price(info['ma_mid'])}"
        f" ｜ MA50 {_fmt_price(info['ma_slow'])}"
    )


def build_alert_message(symbol, kind, states, details, prev_kind=None,
                        prev_summary=None, price=None):
    """
    生成报警文案。

    Args:
        symbol: 交易对
        kind: 'bull' / 'bear' / 'release'
        states: {周期: 状态}
        details: {周期: classify_state 返回的 dict}
        prev_kind: 跳变前的共振方向
        prev_summary: 跳变前的字母摘要
        price: 现价（一般为最短周期收盘价）

    Returns:
        (title, content, template)
    """
    period_lines = [
        _format_period_line(tf, details.get(tf))
        for tf in config.MA_RESONANCE_TIMEFRAMES
    ]
    summary = format_letters(states)
    now_text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if kind == RESONANCE_BULL:
        title = f'📈 趋势转多 · {symbol}'
        template = 'green'
        head = [
            f'**交易对**: {symbol}',
            '**结论**: 1小时级别方向转多，顺势多单可放行',
            f'**合并信号**: {summary}',
        ]
    elif kind == RESONANCE_BEAR:
        title = f'📉 趋势转空 · {symbol}'
        template = 'red'
        head = [
            f'**交易对**: {symbol}',
            '**结论**: 1小时级别方向转空，顺势空单可放行',
        ]
    else:
        title = f'⏸ 趋势解除 · {symbol}'
        template = 'orange'
        prev_text = prev_summary or '未知'
        direction = '多头' if prev_kind == RESONANCE_BULL else '空头'
        head = [
            f'**交易对**: {symbol}',
            f'**结论**: {direction}方向确认失效，暂停顺向入场',
            f'**上次方向**: {prev_text}',
        ]

    if price is not None:
        head.append(f'**现价**: {_fmt_price(price)}')
    head.append(f'**触发时间**: {now_text}')

    content_lines = head + ['', '**各周期状态**'] + period_lines
    content_lines.append('')
    content_lines.append('_本模块为趋势方向过滤器，不构成开仓信号_')
    return title, '\n'.join(content_lines), template


def _make_series(closes):
    """用收盘价序列构造最小可用的K线 DataFrame（仅自检用）"""
    index = pd.date_range(end='2026-01-01', periods=len(closes), freq='5min', tz=None)
    return pd.DataFrame({
        'open': closes,
        'high': closes,
        'low': closes,
        'close': closes,
        'volume': [1.0] * len(closes),
    }, index=index)


if __name__ == '__main__':
    print('=' * 60)
    print('1) 8 种组合判定自检：classify_values(P, MA7, MA25, MA50)')
    print('=' * 60)
    cases = [
        (110, 105, 100, 95, 'L1', 'P>A>B>C        一级多头'),
        (110, 105, 100, 130, 'L2', 'P>A>B，B<C     二级多头'),
        (95, 105, 100, 95, 'L2', 'P<A，A>B>C     二级多头（回踩）'),
        (95, 105, 100, 130, 'L2', 'P<A，A>B，B<C  二级多头'),
        (95, 105, 130, 100, 'S', 'P<A，A<B，B>C  空头'),
        (95, 105, 130, 140, 'S', 'P<A<B<C        空头'),
        (110, 105, 130, 100, None, 'P>A，A<B，B>C  中性'),
        (110, 105, 130, 140, None, 'P>A，A<B，B<C  中性'),
    ]
    failed = 0
    for close, ma7, ma25, ma50, expect, note in cases:
        got = classify_values(close, ma7, ma25, ma50)
        ok = got == expect
        failed += 0 if ok else 1
        print(f"  [{'OK ' if ok else 'FAIL'}] {note:<26} -> {got}")

    print()
    print('=' * 60)
    print('2) 共振判定自检')
    print('=' * 60)
    resonance_cases = [
        ({'1h': 'L1', '15m': 'L1', '5m': 'L1'}, 'bull', '三周期全部一级多头'),
        ({'1h': 'L1', '15m': 'L2', '5m': 'L2'}, 'bull', '三周期混合多头'),
        ({'1h': 'S', '15m': 'S', '5m': 'S'}, 'bear', '三周期全部空头'),
        ({'1h': 'L1', '15m': 'S', '5m': 'L1'}, None, '一周期空头 -> 不共振'),
        ({'1h': 'L1', '15m': None, '5m': 'L1'}, None, '一周期中性 -> 不共振'),
    ]
    for states, expect, note in resonance_cases:
        got = judge_resonance(states)
        ok = got == expect
        failed += 0 if ok else 1
        print(f"  [{'OK ' if ok else 'FAIL'}] {note:<22} -> {got} / 字母 {format_letters(states)}")

    print()
    print('=' * 60)
    print('3) 端到端自检：构造K线序列走完整判定')
    print('=' * 60)
    periods = config.MA_PERIODS

    rising = [100 + i for i in range(60)]
    info = classify_state(_make_series(rising), periods)
    ok = info is not None and info['state'] == 'L1'
    failed += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'FAIL'}] 单调上涨 -> {info['state'] if info else None}（期望 L1）")

    pullback = rising[:-1] + [rising[-2] - 9]
    info = classify_state(_make_series(pullback), periods)
    ok = info is not None and info['state'] == 'L2'
    failed += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'FAIL'}] 上涨后回踩 -> {info['state'] if info else None}（期望 L2）")

    falling = [200 - i for i in range(60)]
    info = classify_state(_make_series(falling), periods)
    ok = info is not None and info['state'] == 'S'
    failed += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'FAIL'}] 单调下跌 -> {info['state'] if info else None}（期望 S）")

    info = classify_state(_make_series([100 + i for i in range(10)]), periods)
    ok = info is None
    failed += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'FAIL'}] 数据不足 -> {info}（期望 None）")

    print()
    print('=' * 60)
    print('4) 未收盘K线剔除自检')
    print('=' * 60)
    tf_ms = timeframe_to_ms('5m')
    raw = pd.DataFrame({
        'open': [100, 101, 102, 103],
        'high': [100, 101, 102, 103],
        'low': [100, 101, 102, 103],
        'close': [100, 101, 102, 103],
        'volume': [1.0] * 4,
    }, index=pd.to_datetime([0, tf_ms, tf_ms * 2, tf_ms * 3], unit='ms'))
    now_ms = tf_ms * 3          # 恰好第三根K线收盘的时刻
    kept = drop_unclosed_candles(raw, '5m', now_ms)
    ok = len(kept) == 3
    failed += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'FAIL'}] 保留已收盘K线 {len(kept)} 根（期望 3，末根仍在走的不算）")

    print()
    print('=' * 60)
    print('5) 文案样例')
    print('=' * 60)
    bull_states = {'1h': 'L1', '15m': 'L1', '5m': 'L1'}
    bull_details = {tf: classify_state(_make_series(rising), periods) for tf in config.MA_RESONANCE_TIMEFRAMES}
    title, content, template = build_alert_message(
        'BTC/USDT', 'bull', bull_states, bull_details, price=159.0
    )
    print(f'标题: {title}')
    print(content)
    print()
    title, content, template = build_alert_message(
        'BTC/USDT', 'release', {'1h': 'L1', '15m': None, '5m': 'L1'},
        bull_details, prev_kind='bull', prev_summary='AA+BB+CC', price=159.0
    )
    print(f'标题: {title}')
    print(content)

    print()
    print('=' * 60)
    print('6) 状态确认（去抖）自检：confirm_scans=2')
    print('=' * 60)
    tracker = ResonanceStateTracker(confirm_scans=2)
    sequence = [
        ('bull', None, '首次判定多头 -> 只记录候选，不切换'),
        ('bull', (None, 'bull'), '连续第2次多头 -> 切换为多头共振'),
        ('bull', None, '维持多头 -> 无事件'),
        (None, None, '单根5m抖动 -> 被吸收，不解除'),
        ('bull', None, '抖动结束回到多头 -> 无事件'),
        (None, None, '第1次转非共振 -> 只记录候选'),
        (None, ('bull', None), '连续第2次转非共振 -> 确认解除'),
        (None, None, '维持非共振 -> 无事件'),
    ]
    for kind, expect_change, note in sequence:
        change = tracker.update(kind)
        ok = change == expect_change
        failed += 0 if ok else 1
        print(f"  [{'OK ' if ok else 'FAIL'}] {note:<34} -> {change}")

    print()
    print('  candidate 持续抖动（bull/None 交替）不应确认任何切换')
    jitter = ResonanceStateTracker(confirm_scans=2)
    outputs = [jitter.update(k) for k in ['bull', None, 'bull', None, 'bull', None]]
    ok = all(o is None for o in outputs)
    failed += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'FAIL'}] 交替输入 6 次 -> {outputs}")

    print()
    print('  confirm_scans=1 应与原始行为一致（立即切换）')
    instant = ResonanceStateTracker(confirm_scans=1)
    first = instant.update('bull')
    ok = first == (None, 'bull')
    failed += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'FAIL'}] 首次判定即切换 -> {first}")

    print()
    print('=' * 60)
    print('7) 按 K线粒度计数自检')
    print('=' * 60)
    tf_ms = timeframe_to_ms('5m')
    base = 1_700_000_000_000 // tf_ms * tf_ms

    print('  同一根K线重复扫描 4 次，confirm_scans=3 不应确认')
    dup = ResonanceStateTracker(confirm_scans=3)
    outputs = [dup.update('bull', base) for _ in range(4)]
    ok = all(o is None for o in outputs) and dup.candidate_count == 1
    failed += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'FAIL'}] 4 次同根扫描 -> {outputs}，计数 {dup.candidate_count}")

    print('  换到下一根K线，累计到第 3 根才确认')
    steps = [dup.update('bull', base + tf_ms * i) for i in (1, 2)]
    ok = steps[0] is None and steps[1] == (None, 'bull')
    failed += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'FAIL'}] 第2根 {steps[0]} / 第3根 {steps[1]}")

    print('  乱序（更早的K线）不应回退计数')
    old = ResonanceStateTracker(confirm_scans=2, formal='bull')
    rollback = [old.update('bear', base), old.update('bear', base - tf_ms)]
    ok = all(o is None for o in rollback) and old.candidate_count == 1
    failed += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'FAIL'}] 早于当前K线的判定被忽略 -> {rollback}，计数 {old.candidate_count}")

    print('  latest_closed_candle_ms 与 drop_unclosed_candles 口径一致')
    now_ms = base + tf_ms + 1000          # 第2根刚收盘 1 秒
    closed = latest_closed_candle_ms('5m', now_ms)
    ok = closed == base
    failed += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'FAIL'}] now={now_ms} -> 最近已收盘K线 {closed}（期望 {base}）")

    print('  中间缺失K线（停机/取数失败）应中断连续性，重新计数')
    gap = ResonanceStateTracker(confirm_scans=3, candle_step_ms=tf_ms)
    gap.update('bull', base)                      # 计数 1
    gap.update('bull', base + tf_ms)              # 计数 2
    gap.update('bull', base + tf_ms * 10)         # 缺 7 根 -> 重置后计数 1
    ok = gap.candidate == 'bull' and gap.candidate_count == 1
    failed += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'FAIL'}] 跨越 9 根后计数重置 -> {gap.candidate_count}（期望 1）")
    tail = [gap.update('bull', base + tf_ms * i) for i in (11, 12)]
    ok = tail[0] is None and tail[1] == (None, 'bull')
    failed += 0 if ok else 1
    print(f"  [{'OK ' if ok else 'FAIL'}] 再连续 2 根后确认 -> {tail[1]}")

    print()
    print('=' * 60)
    print(f"自检结果: {'全部通过' if failed == 0 else f'{failed} 项失败'}")
    print('=' * 60)
