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
- 5分钟自动刷新缓存，30秒前端轮询

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
| ② 微观狙击 | 三道闸门验证 | 均线排列 ✓ 周期共振 ✓ 形态触发 ✓ |
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
| `SYMBOL` | 交易对 | `BTC/USDT` |
| `CACHE_TTL` | 数据缓存时间（秒） | `300` |
| `OBSIDIAN_VAULT_PATH` | Obsidian Vault 根目录路径 | `./obsidian-vault` |

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
├── app.py                 # Flask 后端服务（信号计算 + API）
├── trading-cockpit.html   # 前端驾驶舱页面
├── requirements.txt       # Python 依赖
├── .env.example           # 环境变量模板
├── .gitignore             # Git 忽略规则
└── README.md              # 项目说明文档
```

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

### 强制刷新数据
```
POST /api/refresh
```

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
