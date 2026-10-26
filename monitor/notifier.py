# -*- coding: utf-8 -*-
"""
通知模块
支持飞书消息推送和桌面弹窗通知
支持多周期信号区分
"""
import time
import hmac
import hashlib
import base64
import requests
from datetime import datetime

import config


def get_timeframe_label(timeframe):
    """获取周期的显示名称"""
    tf_config = config.TIMEFRAME_CONFIGS.get(timeframe, {})
    return tf_config.get('label', timeframe)


def gen_feishu_sign(secret, timestamp):
    """生成飞书签名"""
    string_to_sign = f'{timestamp}\n{secret}'
    hmac_code = hmac.new(
        string_to_sign.encode('utf-8'),
        digestmod=hashlib.sha256
    ).digest()
    sign = base64.b64encode(hmac_code).decode('utf-8')
    return sign


def send_feishu_message(title, content, is_markdown=True, template='blue', webhook_url=None):
    """
    发送飞书消息

    Args:
        title: 消息标题
        content: 消息内容
        is_markdown: 是否为markdown格式
        template: 卡片头部颜色 (blue, green, red, orange, purple)
        webhook_url: 自定义Webhook地址（如动量筛选系统的独立群）。不传则用信号监控系统的默认Webhook

    Returns:
        bool: 是否发送成功
    """
    if webhook_url:
        url = webhook_url
    else:
        if not config.ENABLE_FEISHU or not config.FEISHU_WEBHOOK_URL:
            return False
        url = config.FEISHU_WEBHOOK_URL

    try:
        timestamp = str(int(time.time()))
        payload = {
            'msg_type': 'interactive',
            'card': {
                'header': {
                    'title': {
                        'tag': 'plain_text',
                        'content': title
                    },
                    'template': template
                },
                'elements': [
                    {
                        'tag': 'div',
                        'text': {
                            'tag': 'lark_md' if is_markdown else 'plain_text',
                            'content': content
                        }
                    }
                ]
            }
        }

        # 如果配置了签名密钥
        if config.FEISHU_SIGN_SECRET:
            sign = gen_feishu_sign(config.FEISHU_SIGN_SECRET, timestamp)
            payload['timestamp'] = timestamp
            payload['sign'] = sign

        # @所有人
        if config.FEISHU_AT_ALL:
            payload['card']['elements'].append({
                'tag': 'div',
                'text': {
                    'tag': 'lark_md',
                    'content': '<at id=all></at>'
                }
            })

        resp = requests.post(
            url,
            json=payload,
            timeout=10
        )
        result = resp.json()

        if result.get('code') == 0:
            print(f"[飞书推送成功] {title}")
            return True
        else:
            print(f"[飞书推送失败] {result.get('msg', '未知错误')}")
            return False

    except Exception as e:
        print(f"[飞书推送异常] {e}")
        return False


def send_desktop_notification(title, message):
    """
    发送桌面弹窗通知

    Args:
        title: 通知标题
        message: 通知内容

    Returns:
        bool: 是否发送成功
    """
    if not config.ENABLE_DESKTOP_NOTIFY:
        return False

    try:
        from plyer import notification
        notification.notify(
            title=title,
            message=message,
            app_name='交易信号监控',
            timeout=config.DESKTOP_NOTIFY_TIMEOUT
        )
        print(f"[桌面通知成功] {title}")
        return True
    except ImportError:
        print("[桌面通知失败] 未安装 plyer 库，请运行: pip install plyer")
        return False
    except Exception as e:
        print(f"[桌面通知异常] {e}")
        return False


def format_signal_message(signals, symbol, timeframe):
    """
    格式化信号消息

    Args:
        signals: 信号列表
        symbol: 交易对
        timeframe: 时间周期

    Returns:
        tuple: (title, content, template_color)
    """
    if not signals:
        return None, None, 'blue'

    tf_label = get_timeframe_label(timeframe)
    bullish_signals = [s for s in signals if s['direction'] == '看涨']
    bearish_signals = [s for s in signals if s['direction'] == '看跌']

    # 标题和卡片颜色
    if bullish_signals and bearish_signals:
        direction_text = '多空信号交织'
        template = 'orange'
    elif bullish_signals:
        direction_text = '看涨信号'
        template = 'green'
    else:
        direction_text = '看跌信号'
        template = 'red'

    title = f"📊 {symbol} {tf_label} - {direction_text}"

    # 内容
    content_lines = [f"**交易对**: {symbol}"]
    content_lines.append(f"**周期**: {tf_label}")
    content_lines.append(f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    content_lines.append("")

    if bullish_signals:
        content_lines.append("🟢 **看涨形态**:")
        for s in bullish_signals:
            content_lines.append(f"  - {s['pattern']}")
            content_lines.append(f"    {s['description']}")
        content_lines.append("")

    if bearish_signals:
        content_lines.append("🔴 **看跌形态**:")
        for s in bearish_signals:
            content_lines.append(f"  - {s['pattern']}")
            content_lines.append(f"    {s['description']}")
        content_lines.append("")

    if signals:
        content_lines.append(f"**当前价格**: {signals[0]['close_price']:.4f}")

    content = '\n'.join(content_lines)
    return title, content, template


def send_signal_notification(signals, symbol, timeframe):
    """
    发送信号通知（飞书 + 桌面弹窗）

    Args:
        signals: 信号列表
        symbol: 交易对
        timeframe: 时间周期

    Returns:
        dict: 各通道发送结果
    """
    title, content, template = format_signal_message(signals, symbol, timeframe)
    if not title:
        return {}

    results = {}

    # 飞书推送
    if config.ENABLE_FEISHU:
        results['feishu'] = send_feishu_message(title, content, is_markdown=True, template=template)

    # 桌面弹窗
    if config.ENABLE_DESKTOP_NOTIFY:
        # 桌面通知用纯文本
        plain_content = content.replace('**', '').replace('🟢', '[看涨]').replace('🔴', '[看跌]')
        results['desktop'] = send_desktop_notification(title, plain_content)

    return results


def send_hourly_report(symbols_with_signals, total_scanned):
    """
    发送1小时周期扫描报告

    Args:
        symbols_with_signals: 有信号的交易对列表
        total_scanned: 扫描的总交易对数量
    """
    title = f"⏰ 1小时信号报告 - {datetime.now().strftime('%H:%M')}"

    content_lines = [f"**扫描时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"]
    content_lines.append(f"**扫描周期**: 1小时")
    content_lines.append(f"**扫描交易对**: {total_scanned} 个")
    content_lines.append(f"**发现信号**: {len(symbols_with_signals)} 个交易对")
    content_lines.append("")

    if symbols_with_signals:
        content_lines.append("**有信号的交易对**:")
        for item in symbols_with_signals:
            symbol = item['symbol']
            signals = item['signals']
            bullish = [s for s in signals if s['direction'] == '看涨']
            bearish = [s for s in signals if s['direction'] == '看跌']
            parts = []
            if bullish:
                parts.append(f"🟢 {'、'.join([s['pattern'] for s in bullish])}")
            if bearish:
                parts.append(f"🔴 {'、'.join([s['pattern'] for s in bearish])}")
            content_lines.append(f"  **{symbol}**: {' / '.join(parts)}")
    else:
        content_lines.append("本小时暂无明显形态信号，继续观察。")

    content = '\n'.join(content_lines)

    results = {}
    if config.ENABLE_FEISHU:
        results['feishu'] = send_feishu_message(title, content, is_markdown=True, template='blue')

    return results


def send_daily_summary(symbols_with_signals, total_scanned):
    """
    发送4小时周期每日汇总消息

    Args:
        symbols_with_signals: 有信号的交易对列表
        total_scanned: 扫描的总交易对数量
    """
    title = f"📈 4小时信号汇总 - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    content_lines = [f"**扫描时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"]
    content_lines.append(f"**扫描周期**: 4小时")
    content_lines.append(f"**扫描交易对**: {total_scanned} 个")
    content_lines.append(f"**发现信号**: {len(symbols_with_signals)} 个交易对")
    content_lines.append("")

    if symbols_with_signals:
        content_lines.append("**有信号的交易对**:")
        for item in symbols_with_signals:
            symbol = item['symbol']
            signals = item['signals']
            bullish = [s for s in signals if s['direction'] == '看涨']
            bearish = [s for s in signals if s['direction'] == '看跌']
            parts = []
            if bullish:
                parts.append(f"🟢 {'、'.join([s['pattern'] for s in bullish])}")
            if bearish:
                parts.append(f"🔴 {'、'.join([s['pattern'] for s in bearish])}")
            content_lines.append(f"  **{symbol}**: {' / '.join(parts)}")
    else:
        content_lines.append("本次扫描暂无明显形态信号，继续保持观望。")

    content = '\n'.join(content_lines)

    results = {}
    if config.ENABLE_FEISHU:
        results['feishu'] = send_feishu_message(title, content, is_markdown=True, template='purple')

    return results


def send_pre_scan_reminder(timeframe='4h'):
    """发送盘前预备提醒"""
    tf_label = get_timeframe_label(timeframe)
    title = f"⏰ 交易信号监控 - {tf_label}盘前提醒"

    content_lines = [
        f"**提醒时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"即将开始 {tf_label} 周期信号扫描",
        f"**监控交易对**: {len(config.WATCHLIST)} 个",
        f"**监控周期**: {tf_label}",
        f"**监控形态**: {len(config.ENABLED_PATTERNS)} 种",
        "",
        "请做好交易准备，严格执行交易计划。",
    ]

    content = '\n'.join(content_lines)

    if config.ENABLE_FEISHU:
        send_feishu_message(title, content, is_markdown=True, template='blue')

    if config.ENABLE_DESKTOP_NOTIFY:
        send_desktop_notification(title, f"[{tf_label}] 即将扫描 {len(config.WATCHLIST)} 个交易对")


if __name__ == '__main__':
    # 测试
    print("测试通知模块...")
    print(f"飞书推送: {'启用' if config.ENABLE_FEISHU else '禁用'}")
    print(f"桌面弹窗: {'启用' if config.ENABLE_DESKTOP_NOTIFY else '禁用'}")
    print(f"启用周期: {config.ENABLED_TIMEFRAMES}")

    test_signals = [
        {'pattern': '看涨吞没', 'direction': '看涨', 'description': '阳线实体完全覆盖前一根阴线实体', 'close_price': 45000.0},
        {'pattern': '锤子线', 'direction': '看涨', 'description': '长下影线，小实体，底部反转信号', 'close_price': 45000.0},
    ]
    title, content, tmpl = format_signal_message(test_signals, 'BTC/USDT', '1h')
    print(f"\n标题: {title}")
    print(f"颜色: {tmpl}")
    print(f"内容:\n{content}")
