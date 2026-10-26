# -*- coding: utf-8 -*-
"""
交易信号监控系统 - 配置文件
请根据你的实际需求修改以下配置
"""
import os
from dotenv import load_dotenv

# 加载 .env 配置文件（密钥等敏感信息放在 .env 中，不提交到代码库）
load_dotenv()

# ==================== 交易对配置 ====================
# 要监控的交易对列表（交易所格式）
WATCHLIST = [
    'BTC/USDT',      # 比特币
    'ETH/USDT',      # 以太坊
    'SOL/USDT',      # Solana
    'SNDKB/USDT',    # 闪迪（股票代币）
    'SKHYB/USDT',    # SK海力士（股票代币）
    'XAUT/USDT',     # Tether黄金
    'MUB/USDT',      # 美光科技（股票代币）
    'HYPE/USDT',     # HYPE
]

# 获取多少根K线用于形态识别
KLIMIT = 100

# ==================== 多周期监控配置 ====================
# 每个周期独立配置扫描时间和去重窗口
# key 为周期名称（如 '1h', '4h'），value 为该周期的配置
TIMEFRAME_CONFIGS = {
    '1h': {
        'enabled': True,                    # 是否启用
        'label': '1小时',                    # 显示名称
        # 扫描方式：'cron' = 定时cron表达式，'fixed' = 固定时间点，'interval' = 间隔
        'scan_mode': 'cron',
        # cron 表达式（每小时第59分钟扫描，在1小时K线快结束时）
        'cron_minute': 59,                   # 每小时的第几分钟
        # 盘前预备提醒（提前多少分钟）
        'pre_reminder_minutes': 0,           # 1小时周期不需要盘前提醒
        # 去重时间窗口（分钟）：同一交易对同一形态在此时间内只提醒一次
        'deduplication_minutes': 60,         # 1小时
    },
    '4h': {
        'enabled': True,                    # 是否启用
        'label': '4小时',                    # 显示名称
        'scan_mode': 'fixed',               # 固定时间点扫描
        # 每天固定扫描时间列表（24小时制，北京时区）
        'fixed_times': [
            '08:06',   # 早盘提醒（整点后6分钟，等K线稳定）
            '12:06',   # 午间提醒（整点后6分钟，等K线稳定）
            '16:06',   # 下午提醒（整点后6分钟，等K线稳定）
            '20:06',   # 晚间提醒（整点后6分钟，等K线稳定）
            '00:06',   # 午夜提醒（整点后6分钟，等K线稳定）
        ],
        # 盘前预备提醒（提前多少分钟）
        'pre_reminder_minutes': 15,
        # 去重时间窗口（分钟）
        'deduplication_minutes': 240,        # 4小时
    },
}

# 所有启用的周期列表（自动从 TIMEFRAME_CONFIGS 中生成，不用手动改）
ENABLED_TIMEFRAMES = [tf for tf, cfg in TIMEFRAME_CONFIGS.items() if cfg.get('enabled', False)]

# ==================== 数据源配置（多交易所自动降级） ====================
# 交易所列表，按优先级排序（第一个为主数据源，失败后自动尝试下一个）
# 支持的交易所ID（ccxt标准）: binance, gateio, okx, huobi, bybit, kucoin 等
EXCHANGE_LIST = [
    {
        'id': 'binanceusdm',    # 交易所ID（币安U本位永续合约）
        'name': '币安合约',      # 显示名称
        'api_key': os.getenv('BINANCE_API_KEY', ''),
        'api_secret': os.getenv('BINANCE_API_SECRET', ''),
        'timeout': 10000,          # 超时时间（毫秒）
        'enableRateLimit': True,   # 启用速率限制
    },
    {
        'id': 'binance',         # 交易所ID（币安现货）
        'name': '币安现货',       # 显示名称
        'api_key': os.getenv('BINANCE_API_KEY', ''),
        'api_secret': os.getenv('BINANCE_API_SECRET', ''),
        'timeout': 10000,          # 超时时间（毫秒）
        'enableRateLimit': True,   # 启用速率限制
    },
    {
        'id': 'gate',
        'name': 'Gate.io',
        'api_key': os.getenv('GATE_API_KEY', ''),
        'api_secret': os.getenv('GATE_API_SECRET', ''),
        'timeout': 10000,
        'enableRateLimit': True,
    },
]

# 单个交易所失败后重试次数（每个交易所内重试）
EXCHANGE_RETRY_COUNT = 2

# 重试间隔（秒）
EXCHANGE_RETRY_INTERVAL = 1

# 交易对映射（不同交易所的交易对名称可能不同）
# key: 交易所ID，value: {监控名称: 交易所实际名称}
SYMBOL_MAPPING = {
    'binanceusdm': {
        # 股票代币合约：现货带B后缀，合约不带B
        'SNDKB/USDT': 'SNDK/USDT',
        'SKHYB/USDT': 'SKHYNIX/USDT',
        'MUB/USDT': 'MU/USDT',
        # 黄金现货是XAUT，合约也叫XAUT
        # 'XAUT/USDT': 'XAUT/USDT',
    },
}

# ==================== 形态识别配置 ====================
# 启用的形态列表
ENABLED_PATTERNS = [
    '流星线',        # 看跌 - 1根K线
    '乌云盖顶',      # 看跌 - 2根K线
    '黄昏之星',      # 看跌 - 3根K线
    '看跌吞没',      # 看跌 - 2根K线
    '刺透形态',      # 看涨 - 2根K线
    '早晨之星',      # 看涨 - 3根K线
    '看涨吞没',      # 看涨 - 2根K线
    '锤子线',        # 看涨 - 1根K线
]

# 形态确认阈值（实体深入比例，0.5 = 50%）
PATTERN_CONFIRMATION_RATIO = 0.5

# 小实体判定阈值（实体占K线总振幅的比例上限）
SMALL_BODY_RATIO = 0.35

# 长影线判定阈值（影线占实体的比例下限）
LONG_WICK_RATIO = 2.0

# 吞没形态力度倍数（第二根实体 / 第一根实体）
ENGULFING_STRENGTH_RATIO = 1.1

# ==================== 进阶过滤配置（吞没形态专用）====================
# 基于 Gemini 优化版，引入趋势过滤和波动率过滤

# EMA 趋势均线周期
ENGULFING_EMA_PERIOD = 20

# 当前K线实体 / ATR 的最小倍数（反转K线必须有力度）
ENGULFING_CURR_BODY_ATR_RATIO = 0.8

# 开盘容忍比例（加密市场无跳空，允许微小偏差，0.005 = 0.5%）
ENGULFING_OPEN_TOLERANCE = 0.005

# ==================== 进阶过滤配置（锤子线 / 流星线专用）====================
# 新增 EMA20 趋势过滤和 ATR 波动率过滤，大幅降低震荡市假信号

# EMA 趋势均线周期
HAMMER_SHOOTINGSTAR_EMA_PERIOD = 20

# 实体 / ATR 的最大比例（小实体绝对判定：实体必须很小）
HAMMER_SHOOTINGSTAR_BODY_ATR_MAX_RATIO = 0.3

# 长影线 / ATR 的最小比例（影线必须有力度，不是小打小闹）
HAMMER_SHOOTINGSTAR_WICK_ATR_RATIO = 1.0

# 长实体判定（相对近期平均实体的倍数，用于晨星/暮星的第一根）
LONG_BODY_RATIO = 1.5

# 星线反转力度倍数（第三根实体 / 星线实体）
STAR_REVERSAL_RATIO = 2.0

# 短期高低点判断窗口（用于锤子线/流星线的位置确认，K线根数）
SWING_LOOKBACK = 5

# ==================== 进阶过滤配置（乌云盖顶 / 刺透形态专用） ====================
# 基于 Gemini 优化版，引入趋势过滤和波动率过滤，大幅降低噪音

# ATR 周期（平均真实波幅，用于衡量波动率）
ATR_PERIOD = 14

# EMA 趋势均线周期（用于判断当前趋势方向）
PIERCING_DARKCLOUD_EMA_PERIOD = 20

# 大K线过滤器 - 前一根K线实体 / ATR 的最小倍数（过滤震荡市十字星）
# 前一根必须是强有力的大K线，才有反转意义
PIERCING_DARKCLOUD_PREV_BODY_ATR_RATIO = 1.2

# 大K线过滤器 - 当前K线实体 / ATR 的最小倍数
# 当前K线也要有足够力度，不能是小打小闹
PIERCING_DARKCLOUD_CURR_BODY_ATR_RATIO = 0.8

# ==================== 进阶过滤配置（早晨之星 / 黄昏之星专用） ====================
# 基于 Gemini 优化版 + 当前版优势合并，引入趋势过滤、ATR动态衡量、动量衰竭确认

# EMA 趋势均线周期（用于判断当前趋势方向）
STAR_PATTERN_EMA_PERIOD = 20

# 第一根K线实体 / ATR 的最小倍数（趋势确立：必须是大K线）
STAR_PATTERN_C1_BODY_ATR_RATIO = 1.0

# 第三根K线实体 / ATR 的最小倍数（反转确认：必须有力度）
STAR_PATTERN_C3_BODY_ATR_RATIO = 0.8

# 星线实体 / ATR 的最大倍数（动量衰竭：星线必须很小）
STAR_PATTERN_C2_BODY_ATR_MAX_RATIO = 0.3

# 星线动量衰竭阈值：星线收盘价 vs 第一根收盘价的偏离比例上限
# 0.01 = 1%，即星线收盘在第一根收盘价 ±1% 以内，说明动量耗尽
STAR_PATTERN_MOMENTUM_EXHAUSTION_RATIO = 0.01

# 星线反转力度倍数：第三根实体 / 星线实体（越大反转越强）
# 沿用 STAR_REVERSAL_RATIO = 2.0

# ==================== 飞书消息推送配置 ====================
# 是否启用飞书推送
ENABLE_FEISHU = True

# 飞书自定义机器人 Webhook 地址
# 如何获取：飞书群设置 -> 群机器人 -> 添加机器人 -> 自定义机器人 -> 复制Webhook地址
FEISHU_WEBHOOK_URL = os.getenv('FEISHU_WEBHOOK_URL', '')

# 飞书签名校验（如果机器人开启了签名校验，填写密钥）
FEISHU_SIGN_SECRET = os.getenv('FEISHU_SIGN_SECRET', '')

# 动量筛选系统专用飞书 Webhook（推送到独立群，与信号监控系统的飞书群分开）
SCREENER_FEISHU_WEBHOOK_URL = os.getenv('SCREENER_FEISHU_WEBHOOK_URL', '')

# 是否 @所有人
FEISHU_AT_ALL = False

# ==================== 桌面弹窗配置 ====================
# 是否启用桌面弹窗通知
ENABLE_DESKTOP_NOTIFY = True

# 弹窗停留时间（秒）
DESKTOP_NOTIFY_TIMEOUT = 10

# ==================== 网页仪表盘配置 ====================
# 是否启用网页仪表盘
ENABLE_WEB_DASHBOARD = True

# 仪表盘监听地址
WEB_HOST = '0.0.0.0'

# 仪表盘端口
WEB_PORT = 5000

# ==================== 信号记录配置 ====================
# 信号历史记录文件路径
SIGNAL_HISTORY_FILE = 'signal_history.json'

# 最大历史记录条数
MAX_HISTORY_SIGNALS = 1000

# ==================== MA 均线多周期共振配置 ====================
# 独立于K线形态识别系统：自带取数、判定与调度，不修改 TIMEFRAME_CONFIGS，
# 因此不会影响现有形态扫描的行为。
#
# 判定口径（P = 已收盘K线收盘价，A = MA7，B = MA25，C = MA50）：
#   A>B 且 P>A 且 B>C   -> L1 一级多头（1h=AA / 15m=BB / 5m=CC）
#   A>B 且（P<A 或 B<C） -> L2 二级多头（1h=A  / 15m=B  / 5m=C）
#   A<=B 且 P<A         -> S  空头
#   A<=B 且 P>=A        -> 中性（不参与共振）
#
# 共振：三周期全部多头 -> 多头共振；三周期全部空头 -> 空头共振；其余不报警。
# 推送：仅在共振状态发生跳变时推送（建立共振 / 共振解除）。

# 是否启用 MA 共振监控
ENABLE_MA_RESONANCE = True

# 均线周期：MA7 / MA25 / MA50
MA_PERIODS = (7, 25, 50)

# 参与共振判定的周期（顺序即消息中的展示顺序）
MA_RESONANCE_TIMEFRAMES = ('1h', '15m', '5m')

# 周期显示名称
MA_TIMEFRAME_LABELS = {
    '1h': '1小时',
    '15m': '15分钟',
    '5m': '5分钟',
}

# 各周期等级对应的字母标识（一级多头 / 二级多头）
MA_GRADE_LETTERS = {
    '1h': {'L1': 'AA', 'L2': 'A'},
    '15m': {'L1': 'BB', 'L2': 'B'},
    '5m': {'L1': 'CC', 'L2': 'C'},
}

# 每个周期获取多少根K线（MA50 至少需要 50 根，留出充足余量保证均线稳定）
MA_KLIMIT = 120

# 每根 5m K线收盘后的第几秒执行扫描（留出交易所结算时间）
MA_SCAN_SECOND = 20

# 状态确认次数：候选共振状态必须连续 N 次判定一致才正式切换
# N=1 等价于不过滤（原始行为）；N=4 为回测选定值（30天5标的：降噪约50%，
# 共振存续中位数由 5 根 5m（25分钟）提升到 12 根（1小时））
MA_CONFIRM_SCANS = 4

# 解除确认次数：共振解除（回到非共振）需连续 N 根 5m K线确认才推送。
# 进出场不对称设计——进场要快、出场要慢。下跌趋势中 5m 价格短暂反弹到 MA7 上方
# 会让 5m 变“中性”，但 1h/15m 趋势并未改变，12 根（1小时）可过滤这类回踩噪音。
# 设为与 MA_CONFIRM_SCANS 相同则退化为对称确认。
MA_RELEASE_CONFIRM_SCANS = 12

# 共振状态快照文件（绝对路径，跟随本模块目录，避免依赖启动时的 CWD）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MA_STATE_FILE = os.environ.get('MA_STATE_FILE') or os.path.join(BASE_DIR, 'ma_resonance_state.json')

# 最大保留的共振事件条数
MA_MAX_HISTORY = 500

# MA 共振专用的飞书 Webhook（留空则使用默认 FEISHU_WEBHOOK_URL）
MA_FEISHU_WEBHOOK_URL = os.getenv('MA_FEISHU_WEBHOOK_URL', '')

# 是否对 MA 共振事件发送桌面弹窗
MA_ENABLE_DESKTOP_NOTIFY = True
