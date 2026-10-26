"""
共生者交易决策系统 - 后端服务
使用 CCXT 从交易所获取真实 K 线数据，计算技术指标并提供 API
"""
import ccxt
import json
import time
import numpy as np
from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
from datetime import datetime
import os

# 尝试加载 .env 文件（可选依赖）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# ========== 配置 ==========
# 可通过环境变量或 .env 文件配置
EXCHANGE_ID = os.environ.get('EXCHANGE_ID', 'binance')
SYMBOL = os.environ.get('SYMBOL', 'BTC/USDT')
TIMEFRAMES = ['1h', '4h', '8h', '12h', '1d']
# 每个周期需要获取的K线数量（足够计算MA99+形态）
KLINE_LIMIT = 150

# 信号结果缓存（最终计算结果）
_cache = {
    'data': None,
    'last_update': 0,
    'cache_ttl': int(os.environ.get('CACHE_TTL', 300))  # 默认5分钟
}

# K线源无响应导致计算为空时的重试间隔（秒）
# 避免把空结果缓存满 5 分钟，让页面长时间显示 "--"
CACHE_RETRY_TTL = int(os.environ.get('CACHE_RETRY_TTL', 30))


def is_usable(data):
    """数据是否可用：至少拿到当前价格，否则视为 K线获取失败"""
    market = (data or {}).get('market') or {}
    return bool(market.get('current_price'))

# ========== K线缓存层 ==========
# 按周期独立缓存，大周期缓存更久，减少重复请求
# cache_ttl_sec 为基准TTL，各周期按自身时长缩放
_cache_ttl_base = int(os.environ.get('cache_ttl_sec', '60'))  # 基准：1h周期缓存60秒

# 各周期缓存TTL（秒），基于基准值缩放
_KLINE_CACHE_TTL = {
    '1h':  _cache_ttl_base,          # 1h: 60秒
    '4h':  _cache_ttl_base * 3,      # 4h: 180秒
    '8h':  _cache_ttl_base * 6,      # 8h: 360秒
    '12h': _cache_ttl_base * 10,     # 12h: 600秒
    '1d':  _cache_ttl_base * 20,     # 1d: 1200秒
}

# K线缓存存储：{ '1h': {'data': [...], 'ts': 1234567890}, ... }
_kline_cache = {}

# ========== MA 共振后台（只读接入）==========
# 数据来源：monitor/scheduler.py 由 APScheduler 每 5 分钟扫描一次，
# 结果落盘到 monitor/ma_resonance_state.json。驾驶舱只读该文件：
# 不导入监控模块、不触发 scan_all、不写入状态，因此不会与后台进程冲突。
# 默认读取仓库内的 monitor 目录（自包含），可用 MA_STATE_FILE 环境变量覆盖
MA_STATE_FILE = os.path.normpath(
    os.environ.get('MA_STATE_FILE')
    or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'monitor', 'ma_resonance_state.json')
)
MA_SCAN_INTERVAL = int(os.environ.get('MA_SCAN_INTERVAL', 300))   # 后台扫描间隔（秒）
MA_STALE_SECONDS = int(os.environ.get('MA_STALE_SECONDS', 900))   # 超过该时长视为后台停摆
MA_CONFIRM_SCANS = int(os.environ.get('MA_CONFIRM_SCANS', 4))     # 候选共振的确认扫描次数

# 状态文件读取缓存：按 mtime 判断是否变化，避免每次请求都解析 JSON
_ma_state_cache = {'mtime': None, 'data': None}

# 初始化交易所
exchange_config = {
    'enableRateLimit': True,
    'options': {
        'defaultType': 'spot',
    }
}

# 如果配置了 API 密钥则使用
api_key = os.environ.get('EXCHANGE_API_KEY')
api_secret = os.environ.get('EXCHANGE_SECRET')
if api_key and api_secret:
    exchange_config['apiKey'] = api_key
    exchange_config['secret'] = api_secret

# 主交易所不可用（如被限流 418/-1003）时依次尝试的备用交易所
EXCHANGE_FALLBACKS = [
    x.strip() for x in os.environ.get('EXCHANGE_FALLBACKS', 'gate').split(',') if x.strip()
]
EXCHANGE_ORDER = [EXCHANGE_ID] + [x for x in EXCHANGE_FALLBACKS if x != EXCHANGE_ID]

_exchange_clients = {}
# 记录最近一次成功取数的交易所，供页面与健康检查展示
_active_exchange = {'id': EXCHANGE_ID}


def get_exchange(ex_id):
    """按 id 惰性创建并复用 ccxt 客户端"""
    client = _exchange_clients.get(ex_id)
    if client is None:
        client = getattr(ccxt, ex_id)(exchange_config)
        _exchange_clients[ex_id] = client
    return client


# ========== 工具函数 ==========
def sma(values, period):
    """简单移动平均"""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values, period):
    """指数移动平均"""
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    ema_val = sum(values[:period]) / period
    for price in values[period:]:
        ema_val = (price - ema_val) * multiplier + ema_val
    return ema_val


def calc_slope(ma_values, lookback=3):
    """计算均线斜率（返回每根K线的平均变化率百分比）"""
    if len(ma_values) < lookback + 1:
        return 0
    recent = ma_values[-lookback:]
    older = ma_values[-lookback - 1:-1]
    changes = [(r - o) / o * 100 for r, o in zip(recent, older)]
    return sum(changes) / len(changes)


# ========== K线数据获取（带缓存层）==========
def fetch_klines():
    """获取所有周期的K线数据，优先走缓存，仅过期的周期才请求交易所"""
    klines = {}
    now = time.time()
    fetched = []
    cached = []

    for tf in TIMEFRAMES:
        entry = _kline_cache.get(tf)
        ttl = _KLINE_CACHE_TTL.get(tf, _cache_ttl_base)

        if entry and (now - entry['ts']) < ttl:
            klines[tf] = entry['data']
            cached.append(tf)
            continue

        ok = False
        last_error = None
        for ex_id in EXCHANGE_ORDER:
            try:
                client = get_exchange(ex_id)
                ohlcv = client.fetch_ohlcv(SYMBOL, timeframe=tf, limit=KLINE_LIMIT)
                if not ohlcv:
                    raise ValueError('交易所返回空K线')
                klines[tf] = ohlcv
                _kline_cache[tf] = {'data': ohlcv, 'ts': now}
                _active_exchange['id'] = ex_id
                fetched.append(f'{tf}@{ex_id}')
                if ex_id != EXCHANGE_ID:
                    print(f"[降级] {tf} K线改用 {ex_id}（{EXCHANGE_ID} 不可用）")
                time.sleep(client.rateLimit / 1000)
                ok = True
                break
            except Exception as e:
                last_error = e

        if not ok:
            print(f"[ERROR] 获取 {tf} K线失败（已尝试 {', '.join(EXCHANGE_ORDER)}）: {last_error}")
            if entry:
                klines[tf] = entry['data']
                cached.append(tf)
            else:
                klines[tf] = []

    if cached:
        print(f"[CACHE] 命中缓存周期: {', '.join(cached)}")
    if fetched:
        print(f"[FETCH] 请求交易所周期: {', '.join(fetched)}")

    return klines


# ========== 均线计算 ==========
def calc_ma_data(klines):
    """计算各周期的均线数据"""
    ma_data = {}
    for tf in TIMEFRAMES:
        ohlcv = klines.get(tf, [])
        if len(ohlcv) < 99:
            ma_data[tf] = None
            continue
        
        closes = [candle[4] for candle in ohlcv]  # 收盘价
        
        ma7 = sma(closes, 7)
        ma25 = sma(closes, 25)
        ma99 = sma(closes, 99)
        ema50 = ema(closes, 50)
        
        # 计算 MA7 历史值（用于斜率判断）
        ma7_history = []
        for i in range(7, len(closes) + 1):
            ma7_history.append(sum(closes[i-7:i]) / 7)
        
        ma25_history = []
        for i in range(25, len(closes) + 1):
            ma25_history.append(sum(closes[i-25:i]) / 25)
        
        slope7 = calc_slope(ma7_history, 3)
        slope25 = calc_slope(ma25_history, 5)
        
        # 判断方向
        def direction(slope, threshold=0.02):
            if slope > threshold:
                return 'bull'
            elif slope < -threshold:
                return 'bear'
            else:
                return 'neutral'
        
        dir7 = direction(slope7, 0.03)
        dir25 = direction(slope25, 0.02)
        
        # 长期方向：价格 vs MA99
        current_price = closes[-1]
        if current_price > ma99 * 1.005:
            dir99 = 'bull'
        elif current_price < ma99 * 0.995:
            dir99 = 'bear'
        else:
            dir99 = 'neutral'
        
        # 排列判断
        if ma7 > ma25 > ma99:
            alignment = 'bull'
        elif ma7 < ma25 < ma99:
            alignment = 'bear'
        else:
            alignment = 'neutral'
        
        ma_data[tf] = {
            'ma7': round(ma7, 2),
            'ma25': round(ma25, 2),
            'ma99': round(ma99, 2),
            'ema50': round(ema50, 2),
            'slope7': round(slope7, 4),
            'slope25': round(slope25, 4),
            'dir7': dir7,
            'dir25': dir25,
            'dir99': dir99,
            'alignment': alignment,
            'current_price': current_price,
            'closes': closes[-100:]  # 保留最近100根收盘价
        }
    
    return ma_data


# ========== 主周期均线雷达（使用1h周期的均线） ==========
def get_ma_radar(ma_data):
    """获取均线雷达数据（多周期矩阵：1h / 4h / 1d）"""
    radar = {}
    for tf in ['1h', '4h', '1d']:
        if tf not in ma_data or ma_data[tf] is None:
            radar[tf] = None
            continue
        m = ma_data[tf]
        radar[tf] = {
            'ma7': m['ma7'],
            'ma25': m['ma25'],
            'ma99': m['ma99'],
            'dir7': m['dir7'],
            'dir25': m['dir25'],
            'dir99': m['dir99'],
            'alignment': m['alignment'],
            'slope7': m['slope7'],
            'slope25': m['slope25']
        }
    
    # 周期标签（中文 + 时间跨度说明）
    tf_labels = {
        '1h': '1小时',
        '4h': '4小时',
        '1d': '日线'
    }
    
    return {
        'periods': radar,
        'labels': tf_labels,
        'main_period': '1h'  # 主周期
    }


# ========== 多周期趋势共振 ==========
def get_period_resonance(ma_data):
    """判断各周期趋势方向及共振状态"""
    period_dirs = {}
    
    for tf in TIMEFRAMES:
        if tf not in ma_data or ma_data[tf] is None:
            period_dirs[tf] = 'neutral'
            continue
        
        m = ma_data[tf]
        
        # 综合判断方向：均线排列 + MA25斜率 + 价格与MA99关系
        score = 0
        if m['alignment'] == 'bull':
            score += 2
        elif m['alignment'] == 'bear':
            score -= 2
        
        if m['slope25'] > 0.05:
            score += 1
        elif m['slope25'] < -0.05:
            score -= 1
        
        if m['dir99'] == 'bull':
            score += 1
        elif m['dir99'] == 'bear':
            score -= 1
        
        # 大周期权重增加（通过阈值调整）
        if tf in ['1d', '12h']:
            threshold = 1
        else:
            threshold = 1
        
        if score >= threshold:
            period_dirs[tf] = 'bull'
        elif score <= -threshold:
            period_dirs[tf] = 'bear'
        else:
            period_dirs[tf] = 'neutral'
    
    # 计算共振
    bull_count = sum(1 for d in period_dirs.values() if d == 'bull')
    bear_count = sum(1 for d in period_dirs.values() if d == 'bear')
    
    if bull_count >= 4:
        resonance = 'bull'
        resonance_text = '多头共振'
    elif bear_count >= 4:
        resonance = 'bear'
        resonance_text = '空头共振'
    else:
        resonance = 'neutral'
        resonance_text = '分歧中'
    
    return {
        'periods': period_dirs,
        'resonance': resonance,
        'resonance_text': resonance_text,
        'bull_count': bull_count,
        'bear_count': bear_count
    }


# ========== K线形态识别 ==========
def detect_patterns(klines, ma_data):
    """识别K线形态"""
    patterns = []
    
    # --- 底分型（1h周期，3根K线）---
    ohlcv_1h = klines.get('1h', [])
    bottom_fractal = False
    if len(ohlcv_1h) >= 3:
        k1 = ohlcv_1h[-3]  # 左
        k2 = ohlcv_1h[-2]  # 中
        k3 = ohlcv_1h[-1]  # 右
        low1, low2, low3 = k1[3], k2[3], k3[3]
        if low2 < low1 and low2 < low3:
            bottom_fractal = True
    
    patterns.append({
        'id': 'bottom-fractal',
        'name': '底分型',
        'desc': '1h · 3根K线',
        'strength': 'strong',
        'type': 'bull',
        'triggered': bottom_fractal
    })
    
    # --- 射击之星（4h周期，单根K线，强看跌）---
    ohlcv_4h = klines.get('4h', [])
    shooting_star = False
    if len(ohlcv_4h) >= 1:
        k = ohlcv_4h[-1]
        open_p, high_p, low_p, close_p = k[1], k[2], k[3], k[4]
        body = abs(close_p - open_p)
        upper_wick = high_p - max(open_p, close_p)
        lower_wick = min(open_p, close_p) - low_p
        # 射击之星：实体小，上影线长（>=2倍实体），下影线短，阴线
        if body > 0 and upper_wick >= body * 2 and lower_wick < body * 0.5 and close_p < open_p:
            shooting_star = True
    
    patterns.append({
        'id': 'shooting-star',
        'name': '射击之星',
        'desc': '4h · 单根',
        'strength': 'strong',
        'type': 'bear',
        'triggered': shooting_star
    })
    
    # --- 锤子线（4h周期，单根K线，强看涨）---
    hammer = False
    if len(ohlcv_4h) >= 1:
        k = ohlcv_4h[-1]
        open_p, high_p, low_p, close_p = k[1], k[2], k[3], k[4]
        body = abs(close_p - open_p)
        upper_wick = high_p - max(open_p, close_p)
        lower_wick = min(open_p, close_p) - low_p
        # 锤子线：实体小，下影线长（>=2倍实体），上影线短，阳线
        if body > 0 and lower_wick >= body * 2 and upper_wick < body * 0.5 and close_p > open_p:
            hammer = True
    
    patterns.append({
        'id': 'hammer',
        'name': '锤子线',
        'desc': '4h · 单根',
        'strength': 'strong',
        'type': 'bull',
        'triggered': hammer
    })
    
    # --- 吞没形态（8h周期，2根K线）---
    ohlcv_8h = klines.get('8h', [])
    engulfing = False
    engulfing_type = 'bull'  # 默认看涨吞没
    if len(ohlcv_8h) >= 2:
        k1 = ohlcv_8h[-2]
        k2 = ohlcv_8h[-1]
        o1, c1 = k1[1], k1[4]
        o2, c2 = k2[1], k2[4]
        
        # 看涨吞没：前一根阴线，后一根阳线，后实体完全包裹前实体
        if c1 < o1 and c2 > o2 and o2 <= c1 and c2 >= o1:
            engulfing = True
            engulfing_type = 'bull'
        # 看跌吞没
        elif c1 > o1 and c2 < o2 and o2 >= c1 and c2 <= o1:
            engulfing = True
            engulfing_type = 'bear'
    
    patterns.append({
        'id': 'engulfing',
        'name': '吞没形态',
        'desc': '8h · 2根K线',
        'strength': 'strong',
        'type': engulfing_type,
        'triggered': engulfing
    })
    
    # --- 刺透形态（4h周期，2根K线，强看涨）---
    ohlcv_4h = klines.get('4h', [])
    piercing = False
    if len(ohlcv_4h) >= 2:
        k1 = ohlcv_4h[-2]
        k2 = ohlcv_4h[-1]
        o1, h1, l1, c1 = k1[1], k1[2], k1[3], k1[4]
        o2, h2, l2, c2 = k2[1], k2[2], k2[3], k2[4]
        
        # 刺透形态判定：
        # 1. 前一根是大阴线
        # 2. 后一根跳空低开（开盘低于前低）
        # 3. 后一根是大阳线，收盘回升到前阴线实体的50%以上
        prev_body = o1 - c1  # 阴线实体大小
        curr_body = c2 - o2  # 阳线实体大小
        midpoint = (o1 + c1) / 2  # 前阴线实体中点
        
        if (c1 < o1 and  # 前一根阴线
            c2 > o2 and  # 后一根阳线
            o2 < l1 and  # 跳空低开（开盘低于前低）
            c2 > midpoint and  # 收盘站上前阴线实体一半以上
            c2 < o1 and  # 收盘未超过前开盘价（否则就是吞没了）
            prev_body > 0 and
            curr_body >= prev_body * 0.6):  # 阳线实体足够大
            piercing = True
    
    patterns.append({
        'id': 'piercing',
        'name': '刺透形态',
        'desc': '4h · 2根K线',
        'strength': 'strong',
        'type': 'bull',
        'triggered': piercing
    })
    
    # --- 乌云盖顶（4h周期，2根K线，强看跌）---
    dark_cloud = False
    if len(ohlcv_4h) >= 2:
        k1 = ohlcv_4h[-2]
        k2 = ohlcv_4h[-1]
        o1, h1, l1, c1 = k1[1], k1[2], k1[3], k1[4]
        o2, h2, l2, c2 = k2[1], k2[2], k2[3], k2[4]
        
        # 乌云盖顶：刺透形态的反向
        # 1. 前一根是大阳线
        # 2. 后一根跳空高开（开盘高于前高）
        # 3. 后一根是大阴线，收盘跌回前阳线实体的50%以下
        prev_body = c1 - o1  # 阳线实体大小
        curr_body = o2 - c2  # 阴线实体大小
        midpoint = (o1 + c1) / 2  # 前阳线实体中点
        
        if (c1 > o1 and  # 前一根阳线
            c2 < o2 and  # 后一根阴线
            o2 > h1 and  # 跳空高开
            c2 < midpoint and  # 收盘跌破前阳线实体一半
            c2 > o1 and  # 收盘未跌破前开盘价
            prev_body > 0 and
            curr_body >= prev_body * 0.6):
            dark_cloud = True
    
    patterns.append({
        'id': 'dark-cloud',
        'name': '乌云盖顶',
        'desc': '4h · 2根K线',
        'strength': 'strong',
        'type': 'bear',
        'triggered': dark_cloud
    })
    
    # --- 黄昏星（8h周期，3根K线，强看跌）---
    ohlcv_8h = klines.get('8h', [])
    evening_star = False
    if len(ohlcv_8h) >= 3:
        k1 = ohlcv_8h[-3]  # 第一根：大阳线
        k2 = ohlcv_8h[-2]  # 第二根：星线（小实体）
        k3 = ohlcv_8h[-1]  # 第三根：大阴线
        
        o1, h1, l1, c1 = k1[1], k1[2], k1[3], k1[4]
        o2, h2, l2, c2 = k2[1], k2[2], k2[3], k2[4]
        o3, h3, l3, c3 = k3[1], k3[2], k3[3], k3[4]
        
        body1 = c1 - o1   # 第一根阳线实体
        body2 = abs(c2 - o2)  # 第二根星线实体
        body3 = o3 - c3   # 第三根阴线实体
        
        # 黄昏星判定：
        # 1. 第一根大阳线
        # 2. 第二根是小实体星线（实体 < 第一根的30%），且跳空高开
        # 3. 第三根大阴线，收盘深入第一根阳线实体的50%以上
        if (body1 > 0 and  # 第一根阳线
            c1 > o1 and
            body2 < body1 * 0.3 and  # 星线实体小
            min(o2, c2) > c1 and  # 星线跳空高开（高于第一根收盘）
            c3 < o3 and  # 第三根阴线
            body3 >= body1 * 0.6 and  # 阴线实体够大
            c3 < (o1 + c1) / 2):  # 收盘深入第一根实体一半以上
            evening_star = True
    
    patterns.append({
        'id': 'evening-star',
        'name': '黄昏星',
        'desc': '8h · 3根K线',
        'strength': 'strong',
        'type': 'bear',
        'triggered': evening_star
    })
    
    # --- 启明星（8h周期，3根K线，强看涨）---
    morning_star = False
    if len(ohlcv_8h) >= 3:
        k1 = ohlcv_8h[-3]  # 第一根：大阴线
        k2 = ohlcv_8h[-2]  # 第二根：星线（小实体）
        k3 = ohlcv_8h[-1]  # 第三根：大阳线
        
        o1, h1, l1, c1 = k1[1], k1[2], k1[3], k1[4]
        o2, h2, l2, c2 = k2[1], k2[2], k2[3], k2[4]
        o3, h3, l3, c3 = k3[1], k3[2], k3[3], k3[4]
        
        body1 = o1 - c1   # 第一根阴线实体
        body2 = abs(c2 - o2)  # 第二根星线实体
        body3 = c3 - o3   # 第三根阳线实体
        
        # 启明星判定：
        # 1. 第一根大阴线
        # 2. 第二根是小实体星线（实体 < 第一根的30%），且跳空低开
        # 3. 第三根大阳线，收盘深入第一根阴线实体的50%以上
        if (body1 > 0 and  # 第一根阴线
            c1 < o1 and
            body2 < body1 * 0.3 and  # 星线实体小
            max(o2, c2) < c1 and  # 星线跳空低开（低于第一根收盘）
            c3 > o3 and  # 第三根阳线
            body3 >= body1 * 0.6 and  # 阳线实体够大
            c3 > (o1 + c1) / 2):  # 收盘深入第一根实体一半以上
            morning_star = True
    
    patterns.append({
        'id': 'morning-star',
        'name': '启明星',
        'desc': '8h · 3根K线',
        'strength': 'strong',
        'type': 'bull',
        'triggered': morning_star
    })
    
    # --- EMA趋势确认（1D周期，MA7穿MA25）---
    ema_cross = False
    ema_cross_type = 'bull'
    if '1d' in ma_data and ma_data['1d'] is not None:
        m = ma_data['1d']
        closes = m['closes']
        
        # 计算历史 MA7 和 MA25
        ma7_hist = []
        ma25_hist = []
        for i in range(25, len(closes) + 1):
            ma7_hist.append(sum(closes[i-7:i]) / 7 if i >= 7 else None)
            ma25_hist.append(sum(closes[i-25:i]) / 25)
        
        if len(ma7_hist) >= 3 and len(ma25_hist) >= 3:
            prev_ma7 = ma7_hist[-2]
            prev_ma25 = ma25_hist[-2]
            curr_ma7 = ma7_hist[-1]
            curr_ma25 = ma25_hist[-1]
            
            if prev_ma7 and prev_ma25:
                # 金叉
                if prev_ma7 <= prev_ma25 and curr_ma7 > curr_ma25:
                    ema_cross = True
                    ema_cross_type = 'bull'
                # 死叉
                elif prev_ma7 >= prev_ma25 and curr_ma7 < curr_ma25:
                    ema_cross = True
                    ema_cross_type = 'bear'
                # 距离较近也算趋势确认
                elif abs(curr_ma7 - curr_ma25) / curr_ma25 < 0.003:
                    ema_cross = True
                    ema_cross_type = 'bull' if curr_ma7 > curr_ma25 else 'bear'
    
    patterns.append({
        'id': 'ema-cross',
        'name': 'EMA趋势确认',
        'desc': '1D · MA7穿MA25',
        'strength': 'strong',
        'type': ema_cross_type,
        'triggered': ema_cross
    })
    
    return patterns


# ========== MA 共振状态读取（只读）==========
def load_ma_state(history_limit=8):
    """
    读取后台 MA 共振状态文件，返回给驾驶舱展示用的结构化数据。

    只读设计：文件缺失、JSON 损坏、时间戳异常都降级返回 status=unavailable/stale，
    绝不抛异常，也不触发后台扫描。
    """
    result = {
        'status': 'unavailable',          # ok / stale / unavailable
        'state_file': MA_STATE_FILE,
        'scan_interval_sec': MA_SCAN_INTERVAL,
        'stale_after_sec': MA_STALE_SECONDS,
        'confirm_scans': MA_CONFIRM_SCANS,
        'updated_at': None,
        'age_sec': None,
        'next_scan_in_sec': None,
        'scan_count': 0,
        'total_alerts': 0,
        'watch_count': 0,
        'snapshots': [],
        'history': [],
        'message': ''
    }

    try:
        if not os.path.exists(MA_STATE_FILE):
            result['message'] = '未找到状态文件，后台可能尚未启动'
            return result

        mtime = os.path.getmtime(MA_STATE_FILE)
        if _ma_state_cache['data'] is None or _ma_state_cache['mtime'] != mtime:
            with open(MA_STATE_FILE, 'r', encoding='utf-8') as f:
                _ma_state_cache['data'] = json.load(f)
            _ma_state_cache['mtime'] = mtime
        data = _ma_state_cache['data'] or {}
    except Exception as e:
        result['message'] = f'读取状态文件失败：{e}'
        return result

    # ---- 运行健康度 ----
    updated_at = data.get('updated_at')
    age = None
    if updated_at:
        try:
            age = max(0, int(time.time() - datetime.fromisoformat(updated_at).timestamp()))
        except Exception:
            age = None

    if age is None:
        result['status'] = 'unavailable'
        result['message'] = '状态文件缺少有效更新时间'
    elif age > MA_STALE_SECONDS:
        result['status'] = 'stale'
        result['message'] = f'已 {age // 60} 分钟未更新，后台可能已停止'
    else:
        result['status'] = 'ok'
        result['next_scan_in_sec'] = max(0, MA_SCAN_INTERVAL - (age % MA_SCAN_INTERVAL))

    result['updated_at'] = updated_at
    result['age_sec'] = age
    result['scan_count'] = int(data.get('scan_count') or 0)
    result['total_alerts'] = int(data.get('total_alerts') or 0)

    # ---- 各标的快照 ----
    snapshots = []
    for symbol, snap in (data.get('snapshots') or {}).items():
        if not isinstance(snap, dict):
            continue
        candidate_count = int(snap.get('candidate_count') or 0)
        snapshots.append({
            'symbol': snap.get('symbol') or symbol,
            'kind': snap.get('kind'),
            'candidate': snap.get('candidate'),
            'candidate_count': candidate_count,
            'confirm_scans': MA_CONFIRM_SCANS,
            'confirm_progress': f'{min(candidate_count, MA_CONFIRM_SCANS)}/{MA_CONFIRM_SCANS}',
            'summary': snap.get('summary') or '--',
            'states': snap.get('states') or {},
            'updated_at': snap.get('updated_at')
        })

    # 排序：已确认多头 → 已确认空头 → 未确认，同组按标的字母序
    kind_order = {'bull': 0, 'bear': 1}
    snapshots.sort(key=lambda s: (kind_order.get(s['kind'], 2), s['symbol']))
    result['snapshots'] = snapshots
    result['watch_count'] = len(snapshots)

    # ---- 最近事件（倒序，新事件在前）----
    history = list(data.get('history') or [])
    if history_limit and history_limit > 0:
        history = history[-history_limit:]
    events = []
    for ev in reversed(history):
        if not isinstance(ev, dict):
            continue
        events.append({
            'symbol': ev.get('symbol'),
            'kind': ev.get('kind'),
            'summary': ev.get('summary') or '--',
            'price': ev.get('price'),
            'detected_at': ev.get('detected_at')
        })
    result['history'] = events

    return result


def get_ma_gate(ma_state=None):
    """
    取 BTC 的 MA 共振方向，作为微观狙击第三道闸门。

    返回 {kind, summary, available, updated_at, state}；后台未运行或标的不存在时
    available=False，闸门判为不通过（宁可漏一次，不放行一笔）。
    """
    if ma_state is None:
        ma_state = load_ma_state(history_limit=0)

    gate = {
        'kind': None,
        'summary': '--',
        'available': False,
        'updated_at': ma_state.get('updated_at'),
        'states': {}
    }
    for snap in ma_state.get('snapshots', []):
        if snap['symbol'] == SYMBOL or snap['symbol'] == 'BTC/USDT':
            gate['kind'] = snap['kind']
            gate['summary'] = snap['summary']
            gate['states'] = snap['states']
            gate['available'] = ma_state.get('status') == 'ok'
            break
    return gate


# ========== 三段式决策系统 ==========
def calc_decision(ma_data, resonance_data, patterns, ma_gate=None):
    """计算三段式决策结果"""
    
    # === 第一段：宏观雷达 ===
    resonance = resonance_data['resonance']
    
    if resonance == 'bull':
        macro_direction = '多头趋势'
        macro_structure = '上升通道'
        # 计算强度：多头周期数量 / 5
        strength = int(resonance_data['bull_count'] / 5 * 100)
        stage = 2
    elif resonance == 'bear':
        macro_direction = '空头趋势'
        macro_structure = '下降通道'
        strength = int(resonance_data['bear_count'] / 5 * 100)
        stage = 2
    else:
        macro_direction = '震荡整理'
        macro_structure = '区间震荡'
        bull = resonance_data['bull_count']
        bear = resonance_data['bear_count']
        strength = int(max(bull, bear) / 5 * 60)
        stage = 1
    
    macro_strength = max(strength, 30) if stage >= 2 else strength
    
    # === 第二段：微观狙击（三道闸门）===
    gates = {'gate1': False, 'gate2': False, 'gate3': False}
    gate1_text = '均线排列验证'
    gate2_text = '多周期共振确认'
    gate3_text = 'MA共振同向'
    
    if ma_gate is None:
        ma_gate = get_ma_gate()
    
    if stage >= 2:
        # 闸门一：主周期均线排列确认
        main_tf = '1h'
        if main_tf in ma_data and ma_data[main_tf]:
            alignment = ma_data[main_tf]['alignment']
            if (resonance == 'bull' and alignment == 'bull') or \
               (resonance == 'bear' and alignment == 'bear'):
                gates['gate1'] = True
        
        # 闸门二：多周期共振
        gates['gate2'] = resonance in ('bull', 'bear')
        
        # 闸门三：MA 共振方向闸门（1h/15m/5m 三周期共振，来自盯盘后台）
        # 后台未运行（available=False）时视为不通过，避免用陈旧数据放行
        if ma_gate.get('available'):
            if resonance == 'bull':
                gates['gate3'] = ma_gate.get('kind') == 'bull'
            elif resonance == 'bear':
                gates['gate3'] = ma_gate.get('kind') == 'bear'
        
        if all(gates.values()):
            stage = 3
        else:
            stage = 2
    
    # === 第三段：风控与执行 ===
    signal = 'neutral'
    signal_text = '观望中'
    signal_subtext = '系统正在扫描市场机会...'
    entry_price = None
    stop_loss = None
    take_profit = None
    position_size = None
    
    if stage >= 3 and '1h' in ma_data and ma_data['1h']:
        entry_price = ma_data['1h']['current_price']
        is_long = resonance == 'bull'
        
        # 止损：本金10%（按价格比例换算）
        if is_long:
            stop_loss = entry_price * 0.9  # 10%止损
            take_profit = entry_price * 1.25  # 目标止盈
        else:
            stop_loss = entry_price * 1.1
            take_profit = entry_price * 0.8
        
        position_size = '20% 本金'
        
        signal = 'bull' if is_long else 'bear'
        signal_text = '做多信号' if is_long else '做空信号'
        signal_subtext = '宏观{} + 均线排列 + MA共振确认 · 建议入场'.format(
            '多头' if is_long else '空头'
        )
    elif stage == 2:
        passed = sum(1 for v in gates.values() if v)
        signal_text = '等待入场信号'
        signal_subtext = f'微观狙击阶段 · 三道闸门通过 {passed}/3'
    
    return {
        'stage': stage,
        'macro': {
            'direction': macro_direction,
            'strength': macro_strength,
            'structure': macro_structure,
            'scan_periods': '1D / 12h / 8h'
        },
        'gates': gates,
        'gate_labels': {
            'gate1': gate1_text,
            'gate2': gate2_text,
            'gate3': gate3_text
        },
        'ma_gate': ma_gate,
        'signal': {
            'type': signal,
            'text': signal_text,
            'subtext': signal_subtext
        },
        'execution': {
            'entry_price': round(entry_price, 2) if entry_price else None,
            'stop_loss': round(stop_loss, 2) if stop_loss else None,
            'take_profit': round(take_profit, 2) if take_profit else None,
            'position_size': position_size
        }
    }


# ========== 行情数据 ==========
def get_market_data(klines, ma_data):
    """获取市场行情数据"""
    # 从 1h K线获取最新价格
    current_price = None
    ohlcv_1h = klines.get('1h', [])
    if ohlcv_1h:
        current_price = ohlcv_1h[-1][4]
    
    # 计算24h涨跌幅（从1d或1h估算）
    change_24h = 0
    high_24h = 0
    low_24h = 0
    
    if '1d' in ma_data and ma_data['1d']:
        closes = ma_data['1d']['closes']
        if len(closes) >= 2:
            change_24h = (closes[-1] - closes[-2]) / closes[-2] * 100
    
    # 从 1h K线估算24h高低（取最近24根）
    if len(ohlcv_1h) >= 24:
        recent = ohlcv_1h[-24:]
        high_24h = max(k[2] for k in recent)
        low_24h = min(k[3] for k in recent)
    
    # 24h 成交额估算：近 24 根 1h K线的 成交量 × 收盘价
    volume_24h = 0
    if len(ohlcv_1h) >= 24:
        volume_24h = sum(k[5] * k[4] for k in ohlcv_1h[-24:])
    
    return {
        'symbol': SYMBOL,
        'exchange': _active_exchange['id'],
        'current_price': round(current_price, 2) if current_price else None,
        'change_24h': round(change_24h, 2),
        'high_24h': round(high_24h, 2) if high_24h else None,
        'low_24h': round(low_24h, 2) if low_24h else None,
        'volume_24h': round(volume_24h, 2) if volume_24h else None,
    }


# ========== 主计算函数 ==========
def compute_all():
    """计算所有数据"""
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始获取数据...")
    
    # 1. 获取K线
    klines = fetch_klines()
    
    # 2. 计算均线
    ma_data = calc_ma_data(klines)
    
    # 3. 均线雷达
    ma_radar = get_ma_radar(ma_data)
    
    # 4. 多周期共振
    resonance = get_period_resonance(ma_data)
    
    # 5. 形态识别
    patterns = detect_patterns(klines, ma_data)
    
    # 6. MA 共振后台状态（只读，作为第三道闸门）
    ma_state = load_ma_state(history_limit=8)
    ma_gate = get_ma_gate(ma_state)
    
    # 7. 决策系统
    decision = calc_decision(ma_data, resonance, patterns, ma_gate)
    
    # 8. 行情数据
    market = get_market_data(klines, ma_data)
    
    result = {
        'timestamp': int(time.time()),
        'datetime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'market': market,
        'ma_radar': ma_radar,
        'resonance': resonance,
        'patterns': patterns,
        'ma_monitor': ma_state,
        'decision': decision
    }
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 数据计算完成")
    print(f"  - 当前价格: ${market['current_price']}")
    print(f"  - 24h涨跌: {market['change_24h']}%")
    print(f"  - 共振状态: {resonance['resonance_text']}")
    print(f"  - 决策阶段: 第{decision['stage']}段 - {decision['signal']['text']}")
    
    return result


# ========== API 路由 ==========
@app.route('/api/signal')
def get_signal():
    """获取交易信号数据"""
    now = time.time()
    
    # 检查缓存
    if _cache['data'] and (now - _cache['last_update']) < _cache['cache_ttl']:
        result = _cache['data'].copy()
        result['from_cache'] = True
        result['cache_remaining'] = int(_cache['cache_ttl'] - (now - _cache['last_update']))
        return jsonify(result)
    
    try:
        data = compute_all()
        # 只有拿到有效行情才写满 TTL；K线源无响应时只留短暂重试窗口
        if is_usable(data):
            _cache['data'] = data
            _cache['last_update'] = now
            data['cache_remaining'] = _cache['cache_ttl']
        else:
            _cache['data'] = data
            _cache['last_update'] = now - _cache['cache_ttl'] + CACHE_RETRY_TTL
            data['cache_remaining'] = CACHE_RETRY_TTL
            print(f"[WARN] 未取到有效行情（K线源无响应），{CACHE_RETRY_TTL} 秒后重试")
        data['from_cache'] = False
        return jsonify(data)
    except Exception as e:
        print(f"[ERROR] 计算失败: {e}")
        # 如果有缓存数据，返回缓存
        if _cache['data']:
            result = _cache['data'].copy()
            result['from_cache'] = True
            result['error'] = str(e)
            return jsonify(result)
        return jsonify({'error': str(e)}), 500


@app.route('/api/ma_resonance')
def get_ma_resonance():
    """
    MA 共振后台状态（只读）

    展示来源后台（盯盘查看信号报警）的运行情况、各标的确认进度与最近事件。
    history 参数控制返回最近事件条数（0 表示不返回事件）。
    """
    try:
        limit = int(request.args.get('limit', 8))
    except (TypeError, ValueError):
        limit = 8
    limit = max(0, min(limit, 50))
    return jsonify(load_ma_state(history_limit=limit))


@app.route('/api/health')
def health():
    """健康检查"""
    now = time.time()
    kline_status = {}
    for tf in TIMEFRAMES:
        entry = _kline_cache.get(tf)
        ttl = _KLINE_CACHE_TTL.get(tf, _cache_ttl_base)
        if entry:
            remaining = int(ttl - (now - entry['ts']))
            kline_status[tf] = {
                'cached': remaining > 0,
                'remaining_sec': max(remaining, 0),
                'ttl_sec': ttl,
                'bars': len(entry['data'])
            }
        else:
            kline_status[tf] = {'cached': False, 'remaining_sec': 0, 'ttl_sec': ttl, 'bars': 0}

    ma_state = load_ma_state(history_limit=0)
    return jsonify({
        'status': 'ok',
        'exchange': _active_exchange['id'],
        'exchange_order': EXCHANGE_ORDER,
        'symbol': SYMBOL,
        'signal_cache_ttl': _cache['cache_ttl'],
        'signal_last_update': _cache['last_update'],
        'kline_cache_base_ttl': _cache_ttl_base,
        'kline_cache': kline_status,
        'ma_monitor': {
            'status': ma_state['status'],
            'updated_at': ma_state['updated_at'],
            'age_sec': ma_state['age_sec'],
            'scan_count': ma_state['scan_count'],
            'state_file': ma_state['state_file']
        }
    })


# =============================================
#  交易笔记 API（同步到 Obsidian）
# =============================================

# Obsidian 交易日记文件夹路径
# 优先从环境变量读取，可通过 .env 文件配置
# 默认在当前目录下创建 obsidian-vault/交易日记/
_default_vault = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'obsidian-vault')
OBSIDIAN_VAULT_PATH = os.environ.get('OBSIDIAN_VAULT_PATH', _default_vault)
_note_folder_name = os.environ.get('NOTE_FOLDER', '交易日记')
NOTE_FOLDER = os.path.join(OBSIDIAN_VAULT_PATH, _note_folder_name)

# 确保目录存在
os.makedirs(NOTE_FOLDER, exist_ok=True)


def _get_note_path(date_str=None):
    """获取指定日期的笔记文件路径"""
    if date_str is None:
        date_str = datetime.now().strftime('%Y-%m-%d')
    return os.path.join(NOTE_FOLDER, f'{date_str}.md')


def _get_note_content(date_str=None):
    """读取指定日期的笔记内容"""
    path = _get_note_path(date_str)
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    return ''


def _save_note_content(content, date_str=None):
    """保存笔记内容到指定日期的文件"""
    path = _get_note_path(date_str)
    if date_str is None:
        date_str = datetime.now().strftime('%Y-%m-%d')
    
    # 添加 frontmatter（如果是新文件或没有 frontmatter）
    if not content.startswith('---'):
        today = datetime.now().strftime('%Y-%m-%d')
        weekday_cn = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][datetime.now().weekday()]
        frontmatter = f"""---
date: {date_str}
tags: [交易日记, 交易心得]
---

# 交易日记 · {date_str} {weekday_cn}

> BTC 交易记录与市场复盘

"""
        content = frontmatter + content
    
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    return True


def _list_notes(limit=30):
    """列出最近的笔记"""
    if not os.path.exists(NOTE_FOLDER):
        return []
    
    files = []
    for f in os.listdir(NOTE_FOLDER):
        if f.endswith('.md'):
            date_str = f.replace('.md', '')
            path = os.path.join(NOTE_FOLDER, f)
            try:
                with open(path, 'r', encoding='utf-8') as fh:
                    content = fh.read()
                # 提取预览（去掉frontmatter，取前100字）
                preview = content
                if preview.startswith('---'):
                    parts = preview.split('---', 2)
                    if len(parts) >= 3:
                        preview = parts[2].strip()
                preview = preview[:80].replace('\n', ' ') + '...' if len(preview) > 80 else preview
                
                files.append({
                    'date': date_str,
                    'preview': preview,
                    'mtime': os.path.getmtime(path)
                })
            except:
                files.append({
                    'date': date_str,
                    'preview': '',
                    'mtime': os.path.getmtime(path)
                })
    
    # 按日期倒序
    files.sort(key=lambda x: x['date'], reverse=True)
    return files[:limit]


@app.route('/api/note/today')
def get_today_note():
    """获取今日笔记"""
    try:
        content = _get_note_content()
        return jsonify({
            'success': True,
            'date': datetime.now().strftime('%Y-%m-%d'),
            'content': content,
            'note_folder': NOTE_FOLDER
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/note/<date_str>')
def get_note_by_date(date_str):
    """获取指定日期的笔记"""
    try:
        content = _get_note_content(date_str)
        return jsonify({
            'success': True,
            'date': date_str,
            'content': content
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/note/save', methods=['POST'])
def save_note():
    """保存笔记（同步到 Obsidian）"""
    try:
        data = request.get_json()
        content = data.get('content', '').strip()
        
        if not content:
            return jsonify({'success': False, 'error': '内容不能为空'})
        
        date_str = datetime.now().strftime('%Y-%m-%d')
        _save_note_content(content, date_str)
        
        return jsonify({
            'success': True,
            'date': date_str,
            'path': _get_note_path(date_str),
            'folder': NOTE_FOLDER
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/note/list')
def list_notes():
    """获取笔记列表"""
    try:
        notes = _list_notes()
        return jsonify({
            'success': True,
            'notes': notes,
            'folder': NOTE_FOLDER
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/')
def index():
    """驾驶舱首页"""
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'trading-cockpit.html')


@app.route('/api/refresh', methods=['POST'])
def force_refresh():
    """强制刷新数据（清空信号缓存 + K线缓存）"""
    _cache['last_update'] = 0
    _kline_cache.clear()
    return jsonify({'status': 'refreshing', 'message': '信号缓存和K线缓存已清空'})


if __name__ == '__main__':
    print("=" * 50)
    print("共生者交易决策系统 - 后端服务")
    print(f"交易所: {' → '.join(EXCHANGE_ORDER)}（主用不可用时自动降级）")
    print(f"交易对: {SYMBOL}")
    print(f"周期: {', '.join(TIMEFRAMES)}")
    print(f"信号缓存: {_cache['cache_ttl']}秒")
    print(f"K线缓存基准: {_cache_ttl_base}秒")
    for tf in TIMEFRAMES:
        print(f"  {tf}: TTL={_KLINE_CACHE_TTL.get(tf, _cache_ttl_base)}秒")
    print(f"MA共振后台(只读): {MA_STATE_FILE}")
    print("=" * 50)
    
    # 启动时先计算一次
    try:
        data = compute_all()
        if is_usable(data):
            _cache['data'] = data
            _cache['last_update'] = time.time()
            print("\n初始数据加载完成！")
        else:
            # 不写入完整缓存，服务起来后按 CACHE_RETRY_TTL 自动重试
            print("\n初始行情未就绪（K线源无响应），服务启动后将自动重试")
    except Exception as e:
        print(f"\n初始加载失败: {e}")
    
    # 启动服务
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 5001))
    app.run(host=host, port=port, debug=False)
