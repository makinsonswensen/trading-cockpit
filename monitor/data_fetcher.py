# -*- coding: utf-8 -*-
"""
数据获取模块（多交易所自动降级版）
支持多个交易所数据源，主数据源失败时自动切换到下一个

降级策略（三级）：
1. 币安U本位永续合约（binanceusdm）- 优先
2. 币安现货（binance）- 第一降级
3. Gate.io（gate）- 最终降级

通用机制：
- 按 EXCHANGE_LIST 顺序尝试
- 每个交易所内重试 EXCHANGE_RETRY_COUNT 次
- 全部失败则返回 None，记录错误日志
"""
import ccxt
import pandas as pd
import time
import config


class MultiExchangeFetcher:
    """多交易所数据获取器，支持自动降级"""

    def __init__(self):
        self.exchanges = {}          # 已初始化的交易所实例 {exchange_id: exchange_obj}
        self.exchange_configs = {}    # 交易所配置 {exchange_id: config_dict}
        self.last_used_exchange = {}  # 每个交易对最后成功的交易所 {symbol: exchange_id}
        self.failed_exchanges = {}    # 全局失败的交易所 {exchange_id: fail_timestamp}
        self.failed_pairs = {}        # 按交易对+交易所失败记录 {(exchange_id, symbol): fail_timestamp}
        self._init_exchanges()

    def _init_exchanges(self):
        """初始化所有交易所连接（只初始化，不测试连接）"""
        for ex_config in config.EXCHANGE_LIST:
            ex_id = ex_config['id']
            try:
                ex_class = getattr(ccxt, ex_id)
                exchange = ex_class({
                    'apiKey': ex_config.get('api_key', ''),
                    'secret': ex_config.get('api_secret', ''),
                    'timeout': ex_config.get('timeout', 10000),
                    'enableRateLimit': ex_config.get('enableRateLimit', True),
                })
                self.exchanges[ex_id] = exchange
                self.exchange_configs[ex_id] = ex_config
                print(f"[数据源] 交易所 {ex_config['name']} ({ex_id}) 已初始化")
            except Exception as e:
                print(f"[数据源警告] 初始化交易所 {ex_id} 失败: {e}")

    def _map_symbol(self, symbol, exchange_id):
        """根据映射表转换交易对名称"""
        mapping = config.SYMBOL_MAPPING.get(exchange_id, {})
        return mapping.get(symbol, symbol)

    def _is_exchange_available(self, exchange_id):
        """检查交易所全局是否可用（冷却时间是否已过）"""
        if exchange_id not in self.failed_exchanges:
            return True
        cooldown = 300  # 5分钟
        last_fail = self.failed_exchanges[exchange_id]
        if time.time() - last_fail < cooldown:
            return False
        del self.failed_exchanges[exchange_id]
        return True

    def _is_pair_available(self, exchange_id, symbol):
        """检查某个交易对在某个交易所是否可用"""
        key = (exchange_id, symbol)
        if key not in self.failed_pairs:
            return True
        # 交易对级别的冷却时间更长（1小时），因为"没有这个交易对"是长期状态
        cooldown = 3600  # 1小时
        last_fail = self.failed_pairs[key]
        if time.time() - last_fail < cooldown:
            return False
        del self.failed_pairs[key]
        return True

    def _mark_exchange_failed(self, exchange_id):
        """标记交易所全局失败"""
        self.failed_exchanges[exchange_id] = time.time()

    def _mark_pair_failed(self, exchange_id, symbol):
        """标记某个交易对在某个交易所失败"""
        key = (exchange_id, symbol)
        self.failed_pairs[key] = time.time()

    def fetch_ohlcv(self, symbol, timeframe='1h', limit=100, since=None):
        """
        获取K线数据（带自动降级）

        Args:
            symbol: 交易对（如 'BTC/USDT'）
            timeframe: K线周期
            limit: K线数量
            since: 起始时间戳（毫秒），None 表示取最近 limit 根；回测分页时使用

        Returns:
            DataFrame or None: K线数据
        """
        # 确定尝试顺序：优先使用上次成功的交易所
        exchange_order = self._get_try_order(symbol)

        for ex_id in exchange_order:
            # 检查交易所全局是否可用
            if not self._is_exchange_available(ex_id):
                continue

            # 检查这个交易对在这个交易所是否可用
            if not self._is_pair_available(ex_id, symbol):
                continue

            if ex_id not in self.exchanges:
                continue

            exchange = self.exchanges[ex_id]
            ex_name = self.exchange_configs.get(ex_id, {}).get('name', ex_id)
            mapped_symbol = self._map_symbol(symbol, ex_id)

            last_error = None
            is_symbol_error = False

            # 每个交易所内重试多次
            for attempt in range(config.EXCHANGE_RETRY_COUNT):
                try:
                    ohlcv = exchange.fetch_ohlcv(
                        mapped_symbol, timeframe=timeframe, limit=limit, since=since
                    )

                    if ohlcv and len(ohlcv) > 0:
                        df = self._ohlcv_to_dataframe(ohlcv)
                        # 记录成功的交易所
                        self.last_used_exchange[symbol] = ex_id
                        # 如果不是第一个交易所成功，打印降级提示
                        primary_id = config.EXCHANGE_LIST[0]['id'] if config.EXCHANGE_LIST else ''
                        if ex_id != primary_id:
                            print(f"[数据源降级] {symbol} ({timeframe}) 从 {config.EXCHANGE_LIST[0]['name']} 降级到 {ex_name}")
                        return df

                except Exception as e:
                    err_msg = str(e)
                    last_error = err_msg

                    # 判断是否为"交易对不存在"类错误
                    lower_err = err_msg.lower()
                    if any(keyword in lower_err for keyword in [
                        'does not have market symbol',
                        'market symbol',
                        'not found',
                        'invalid symbol',
                        'unknown symbol',
                    ]):
                        is_symbol_error = True
                        # 交易对不存在，不需要重试
                        break

                    # 最后一次重试失败才打印
                    if attempt == config.EXCHANGE_RETRY_COUNT - 1:
                        print(f"[数据源错误] {ex_name} 获取 {mapped_symbol} {timeframe} K线失败: {err_msg[:100]}")
                    else:
                        time.sleep(config.EXCHANGE_RETRY_INTERVAL)

            # 判断失败类型
            if is_symbol_error:
                # 交易对不存在，标记为交易对级别的失败（长时间冷却）
                self._mark_pair_failed(ex_id, symbol)
                print(f"[数据源提示] {ex_name} 不支持交易对 {mapped_symbol}，已跳过")
            else:
                # 网络或其他临时性错误，标记交易所全局失败（短时间冷却）
                self._mark_exchange_failed(ex_id)

        # 所有交易所都失败了
        print(f"[数据源严重错误] 所有交易所都无法获取 {symbol} ({timeframe}) 的K线数据")
        return None

    def _get_try_order(self, symbol):
        """
        获取交易所尝试顺序
        优先使用上次成功的交易所，然后按配置顺序
        """
        order = []
        # 上次成功的排第一
        last_success = self.last_used_exchange.get(symbol)
        if last_success:
            order.append(last_success)

        # 然后按配置顺序加入剩下的
        for ex_config in config.EXCHANGE_LIST:
            ex_id = ex_config['id']
            if ex_id not in order:
                order.append(ex_id)

        return order

    def _ohlcv_to_dataframe(self, ohlcv):
        """将ccxt返回的OHLCV数据转换为DataFrame"""
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        # 确保数据类型正确
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df.dropna(inplace=True)
        return df

    def test_connection(self):
        """
        测试数据源连接是否可用
        只要有至少一个交易所能成功获取BTC数据，就算可用

        Returns:
            bool: 是否至少有一个交易所可用
        """
        if not self.exchanges:
            print("[数据源错误] 没有可用的交易所")
            return False

        # 用第一个交易对做测试
        test_symbol = config.WATCHLIST[0] if config.WATCHLIST else 'BTC/USDT'
        test_timeframe = '1h'
        test_limit = 5

        for ex_config in config.EXCHANGE_LIST:
            ex_id = ex_config['id']
            ex_name = ex_config.get('name', ex_id)

            if ex_id not in self.exchanges:
                continue

            exchange = self.exchanges[ex_id]
            mapped_symbol = self._map_symbol(test_symbol, ex_id)

            try:
                ohlcv = exchange.fetch_ohlcv(
                    mapped_symbol, timeframe=test_timeframe, limit=test_limit
                )
                if ohlcv and len(ohlcv) > 0:
                    print(f"[数据源] {ex_name} 连接正常 (测试: {mapped_symbol})")
                    return True
            except Exception as e:
                print(f"[数据源] {ex_name} 连接测试失败: {str(e)[:80]}")

        print("[数据源错误] 所有交易所都无法连接")
        return False

    def get_exchange_status(self):
        """获取所有交易所的状态信息（用于仪表盘展示）"""
        status = []
        for ex_config in config.EXCHANGE_LIST:
            ex_id = ex_config['id']
            ex_name = ex_config.get('name', ex_id)
            initialized = ex_id in self.exchanges
            available = self._is_exchange_available(ex_id)

            # 统计这个交易所服务了多少个交易对
            served_count = sum(
                1 for s, eid in self.last_used_exchange.items() if eid == ex_id
            )

            status.append({
                'id': ex_id,
                'name': ex_name,
                'initialized': initialized,
                'available': available,
                'served_symbols': served_count,
                'cooling_down': not available,
            })
        return status

    def get_symbol_source_map(self):
        """获取每个交易对当前使用的数据源"""
        return dict(self.last_used_exchange)


# 全局单例
_fetcher_instance = None


def get_data_fetcher():
    """获取数据获取器单例"""
    global _fetcher_instance
    if _fetcher_instance is None:
        _fetcher_instance = MultiExchangeFetcher()
    return _fetcher_instance


if __name__ == '__main__':
    """测试多交易所降级功能"""
    fetcher = get_data_fetcher()

    print("\n" + "=" * 60)
    print("测试多交易所降级功能")
    print("=" * 60)

    test_symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']

    for symbol in test_symbols:
        print(f"\n--- {symbol} 1h ---")
        df = fetcher.fetch_ohlcv(symbol, '1h', limit=50)
        if df is not None:
            print(f"  成功获取 {len(df)} 根K线")
            print(f"  最新收盘价: {df.iloc[-1]['close']:.2f}")
            source = fetcher.last_used_exchange.get(symbol, '未知')
            print(f"  数据源: {source}")
        else:
            print("  获取失败")

    print("\n" + "=" * 60)
    print("交易所状态")
    print("=" * 60)
    for status in fetcher.get_exchange_status():
        state = "✅ 可用" if status['available'] else "❌ 冷却中"
        print(f"  {status['name']}: {state} (服务 {status['served_symbols']} 个交易对)")
