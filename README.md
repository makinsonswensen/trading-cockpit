# 共生者交易决策系统 · 交易驾驶舱

> 基于道家"共生"思想的三段式交易决策系统，通过宏观判向、微观狙击、风控执行三阶段，结合多周期共振与K线形态识别，输出高质量交易信号。

![Python](https://img.shields.io/badge/Python-3.9+-blue)
![Flask](https://img.shields.io/badge/Flask-3.0+-green)
![CCXT](https://img.shields.io/badge/CCXT-4.0+-orange)
![License](https://img.shields.io/badge/license-MIT-yellow)

---

## 功能特性

### 实时行情模块
- 接入 CCXT，支持 Binance / OKX / Bybit 等 100+ 交易所
- 实时价格、24h涨跌幅、最高/最低、成交量、市值
- 两层缓存架构，减少交易所请求

### 均线雷达（多周期矩阵）
- 1h / 4h / 1d 三周期均线矩阵
- MA7 / MA25 / MA99 三条均线
- 均线方向实时判断（向上/向下/走平）
- 排列状态识别（多头排列 / 空头排列 / 分歧）

### 多周期趋势共振
- 1h / 4h / 8h / 12h / 1d 五周期趋势判向
- 基于均线排列、MA斜率、价格位置三重验证
- 五浪共振状态实时监测（多头共振 / 空头共振 / 分歧）

### K线形态预警
- **9种强形态**实时识别
- 看涨：底分型、锤子线、刺透形态、看涨吞没、启明星、EMA金叉
- 看跌：射击之星、乌云盖顶、黄昏星、看跌吞没、EMA死叉
- 只显示已触发形态，静默形态不干扰

### 三段式决策系统
| 阶段 | 功能 | 说明 |
|------|------|------|
| ① 宏观雷达 | 大周期趋势判向 | 方向 / 强度 / 市场结构 |
| ② 微观狙击 | 三道闸门验证 | 均线排列 ✓ 周期共振 ✓ MA共振 ✓ |
| ③ 风控执行 | 止损止盈管理 | 入场价 / 止损位 / 止盈位 / 仓位 |

### 交易心得笔记
- 内置交易日记编辑器
- 自动同步到 Obsidian Vault
- 标准 Markdown 格式，带 YAML frontmatter
- 历史笔记浏览与回显

---

## 快速开始

### 环境要求
- Python 3.9+
- pip

### 安装步骤

1. **克隆项目**
```bash
git clone <your-repo-url>
cd trading-cockpit
```

2. **创建虚拟环境（推荐）**
```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
```

3. **安装依赖**
```bash
pip install -r requirements.txt
```

4. **配置环境变量**
```bash
# 复制配置模板
copy .env.example .env
# 编辑 .env，设置你的 Obsidian Vault 路径等
```

5. **启动服务**
```bash
# 方式一：使用启动脚本（推荐，自动检查依赖和配置）
# Windows
start.bat
# macOS/Linux
chmod +x start.sh && ./start.sh

# 方式二：直接运行
python app.py
```

6. **打开驾驶舱**

浏览器访问 `http://localhost:5001`

---

## 配置说明

### 环境变量（.env）

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `EXCHANGE_ID` | 交易所 ID（ccxt 支持的交易所） | `binance` |
| `EXCHANGE_FALLBACKS` | 主用交易所被限流/连不上时依次切换的备用所（逗号分隔） | `gate` |
| `SYMBOL` | 交易对 | `BTC/USDT` |
| `CACHE_TTL` | 信号结果缓存时间（秒） | `300` |
| `cache_ttl_sec` | K线缓存基准TTL（秒），各周期按此缩放 | `60` |
| `OBSIDIAN_VAULT_PATH` | Obsidian Vault 根目录路径 | `./obsidian-vault` |
| `NOTE_FOLDER` | 交易日记子文件夹名 | `交易日记` |
| `HOST` | 服务监听地址 | `0.0.0.0` |
| `PORT` | 服务端口 | `5001` |

### 缓存架构

系统采用两层缓存，减少对交易所的重复请求：

```
请求 /api/signal
  │
  ├─ 信号缓存未过期 → 直接返回（不触发K线获取）
  │    TTL = CACHE_TTL (默认300秒)
  │
  └─ 信号缓存过期 → 调用 fetch_klines()
       │
       ├─ 1h K线缓存过期?  → 请求交易所
       ├─ 4h K线缓存过期?  → 请求交易所
       ├─ 8h K线缓存未过期? → 走缓存
       ├─ 12h K线缓存未过期? → 走缓存
       └─ 1d K线缓存未过期? → 走缓存
```

K线各周期缓存TTL基于 `cache_ttl_sec` 缩放：

| 周期 | TTL公式 | 默认值 | 说明 |
|------|---------|--------|------|
| 1h | `cache_ttl_sec × 1` | 60s | 变化最快，缓存最短 |
| 4h | `cache_ttl_sec × 3` | 180s | |
| 8h | `cache_ttl_sec × 6` | 360s | |
| 12h | `cache_ttl_sec × 10` | 600s | |
| 1d | `cache_ttl_sec × 20` | 1200s | 变化最慢，缓存最长 |

### 可配置参数（app.py）

```python
EXCHANGE_ID = 'binance'       # 交易所
SYMBOL = 'BTC/USDT'           # 交易对
TIMEFRAMES = ['1h', '4h', '8h', '12h', '1d']  # 监控周期
KLINE_LIMIT = 150             # 每周期K线数量
```

---

## 项目结构

```
trading-cockpit/
├── app.py                 # Flask 后端服务（信号计算 + API + 两层缓存）
├── trading-cockpit.html   # 前端驾驶舱页面
├── monitor/               # MA 共振后台（自包含，独立进程）
│   ├── scheduler.py       # 每 5 分钟（5m K线收盘后）触发一次扫描的调度器
│   ├── ma_monitor.py      # 扫描主流程：取数 → 判级 → 共振 → 推送 → 落盘状态
│   ├── ma_resonance.py    # 均线判定与共振逻辑、消息文案
│   ├── data_fetcher.py    # CCXT 多交易所取数与降级
│   ├── notifier.py        # 飞书 Webhook / 桌面弹窗推送
│   ├── config.py          # 监控配置（标的、周期、阈值、Webhook）
│   └── ma_resonance_state.json  # 运行时状态（自动生成，已 gitignore）
├── start.bat              # Windows 启动脚本（MA 后台 + 驾驶舱）
├── start.sh               # macOS/Linux 启动脚本（MA 后台 + 驾驶舱）
├── requirements.txt       # Python 依赖
├── .env.example           # 环境变量模板（提交到 Git）
├── .env                   # 本地配置（不提交，已 gitignore）
├── .gitignore             # Git 忽略规则
└── README.md              # 项目说明文档
```

---

## MA 共振后台（monitor/）

- **来源**：从独立项目 `盯盘查看信号报警` 精简迁入，只保留 1h / 15m / 5m 三周期 MA 共振判定与推送，
  原有的形态扫描、动量筛选、日报、网页仪表盘等均未迁入。
- **运行**：`start.bat`（或 `start.sh`）会同时起 MA 后台与驾驶舱；也可单独运行
  `python monitor/scheduler.py`。后台在每根 5m K线收盘后的第 20 秒扫描一次。
- **状态文件**：`monitor/ma_resonance_state.json`（运行时生成，已 gitignore），
  保存各标的确认进度与共振事件历史，供驾驶舱 `GET /api/ma_resonance` 只读展示。
- **与驾驶舱的关系**：驾驶舱只读该 JSON，不 import 监控模块、不触发扫描、不写文件，
  二者进程隔离、互不冲突。后台停摆超过 `MA_STALE_SECONDS` 时接口状态变为 `stale`。
- **环境变量**：`MA_STATE_FILE`（可覆盖状态文件路径）、
  `MA_SCAN_INTERVAL` / `MA_STALE_SECONDS` / `MA_CONFIRM_SCANS` 已在 `.env.example` 说明；
  飞书推送地址在 `.env` 中配置（`FEISHU_WEBHOOK_URL`，可选 `MA_FEISHU_WEBHOOK_URL` 单独指定）。

---

## API 接口

### 获取交易信号
```
GET /api/signal
```

返回所有计算好的信号数据：行情、均线、共振、形态、决策阶段。

### 健康检查
```
GET /api/health
```

返回信号缓存状态和每个周期的K线缓存状态（命中/过期、剩余秒数、TTL、K线数量）。

### MA 共振后台状态
```
GET /api/ma_resonance?limit=8
```

只读展示「盯盘查看信号报警」后台的运行情况：状态（ok / stale / unavailable）、
扫描次数、累计事件、上次扫描距今秒数、下次扫描倒计时、各标的确认进度（x/4）
与最近共振事件。`limit` 控制返回的事件条数（0-50）。

该接口只读 `ma_resonance_state.json`，不导入对方模块、不触发扫描、不写入文件，
因此不会与后台进程冲突；后台停摆超过 `MA_STALE_SECONDS` 时状态变为 `stale`，
此时微观狙击的第三道闸门（MA共振同向）判为不通过。

### 强制刷新数据
```
POST /api/refresh
```

清空信号缓存和K线缓存，下次请求将重新从交易所获取全部数据。

### 交易笔记

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/note/today` | GET | 获取今日笔记 |
| `/api/note/<date>` | GET | 获取指定日期笔记 |
| `/api/note/save` | POST | 保存笔记（同步到 Obsidian） |
| `/api/note/list` | GET | 获取笔记列表 |

---

## 形态识别清单

### 强看涨形态（6种）
| 形态 | 周期 | K线数 | 说明 |
|------|------|-------|------|
| 底分型 | 1h | 3根 | 中间K最低价低于左右 |
| 锤子线 | 4h | 1根 | 下影线≥2倍实体 |
| 刺透形态 | 4h | 2根 | 阴线后跳空低开阳线站一半 |
| 看涨吞没 | 8h | 2根 | 阳线实体完全包裹阴线 |
| 启明星 | 8h | 3根 | 底部三星反转 |
| EMA金叉 | 1D | 均线 | MA7上穿MA25 |

### 强看跌形态（5种）
| 形态 | 周期 | K线数 | 说明 |
|------|------|-------|------|
| 射击之星 | 4h | 1根 | 上影线≥2倍实体 |
| 乌云盖顶 | 4h | 2根 | 阳线后跳空高开阴线跌一半 |
| 黄昏星 | 8h | 3根 | 顶部三星反转 |
| 看跌吞没 | 8h | 2根 | 阴线实体完全包裹阳线 |
| EMA死叉 | 1D | 均线 | MA7下穿MA25 |

---

## 交易理念

> 人法地，地法天，天法道，道法自然。 —— 《道德经》

本系统基于道家"共生"思想设计：

- **顺势而为**：宏观判向，只做顺势单，逆势禁止开仓
- **伺机而动**：三道闸门验证，等待最佳入场时机
- **知止不殆**：严格止损止盈，风控优先于收益
- **有生于无**：从无信号到有信号，再从有信号到平仓，循环往复

---

## 免责声明

本系统仅供学习和研究使用，不构成任何投资建议。
加密货币交易存在高风险，请谨慎决策，盈亏自负。

---

## License

MIT License
