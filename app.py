"""
共生者交易决策系统 - 后端服务
使用 CCXT 从交易所获取真实 K 线数据，计算技术指标并提供 API
"""
import ccxt
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

# 全局缓存
_cache = {
    'data': None,
    'last_update': 0,
    'cache_ttl': int(os.environ.get('CACHE_TTL', 300))  # 默认5分钟缓存
}

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

exchange = getattr(ccxt, EXCHANGE_ID)(exchange_config)


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


# ========== K线数据获取 ==========
def fetch_klines():
    """获取所有周期的K线数据"""
    klines = {}
    for tf in TIMEFRAMES:
        try:
            ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe=tf, limit=KLINE_LIMIT)
            klines[tf] = ohlcv
            print(f"[OK] 获取 {tf} K线: {len(ohlcv)} 根")
        except Exception as e:
            print(f"[ERROR] 获取 {tf} K线失败: {e}")
            klines[tf] = []
        time.sleep(exchange.rateLimit / 1000)
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


# ========== 三段式决策系统 ==========
def calc_decision(ma_data, resonance_data, patterns):
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
    gate3_text = 'K线形态触发'
    
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
        
        # 闸门三：形态触发（需要有同向的强形态触发）
        if resonance == 'bull':
            strong_bull_patterns = [p for p in patterns 
                                    if p['type'] == 'bull' and p['triggered'] and p['strength'] == 'strong']
            gates['gate3'] = len(strong_bull_patterns) > 0
        elif resonance == 'bear':
            strong_bear_patterns = [p for p in patterns 
                                    if p['type'] == 'bear' and p['triggered'] and p['strength'] == 'strong']
            gates['gate3'] = len(strong_bear_patterns) > 0
        
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
        signal_subtext = '宏观{} + 均线排列 + 形态确认 · 建议入场'.format(
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
    
    return {
        'symbol': SYMBOL,
        'exchange': EXCHANGE_ID,
        'current_price': round(current_price, 2) if current_price else None,
        'change_24h': round(change_24h, 2),
        'high_24h': round(high_24h, 2) if high_24h else None,
        'low_24h': round(low_24h, 2) if low_24h else None,
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
    
    # 6. 决策系统
    decision = calc_decision(ma_data, resonance, patterns)
    
    # 7. 行情数据
    market = get_market_data(klines, ma_data)
    
    result = {
        'timestamp': int(time.time()),
        'datetime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'market': market,
        'ma_radar': ma_radar,
        'resonance': resonance,
        'patterns': patterns,
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
        _cache['data'] = data
        _cache['last_update'] = now
        data['from_cache'] = False
        data['cache_remaining'] = _cache['cache_ttl']
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


@app.route('/api/health')
def health():
    """健康检查"""
    return jsonify({
        'status': 'ok',
        'exchange': EXCHANGE_ID,
        'symbol': SYMBOL,
        'cache_ttl': _cache['cache_ttl'],
        'last_update': _cache['last_update']
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
    """强制刷新数据"""
    _cache['last_update'] = 0  # 使缓存失效
    return jsonify({'status': 'refreshing'})


if __name__ == '__main__':
    print("=" * 50)
    print("共生者交易决策系统 - 后端服务")
    print(f"交易所: {EXCHANGE_ID}")
    print(f"交易对: {SYMBOL}")
    print(f"周期: {', '.join(TIMEFRAMES)}")
    print(f"缓存: {_cache['cache_ttl']}秒")
    print("=" * 50)
    
    # 启动时先计算一次
    try:
        data = compute_all()
        _cache['data'] = data
        _cache['last_update'] = time.time()
        print("\n初始数据加载完成！")
    except Exception as e:
        print(f"\n初始加载失败: {e}")
    
    # 启动服务
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 5001))
    app.run(host=host, port=port, debug=False)
