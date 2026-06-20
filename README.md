# A 股分析系统 (A-Share Analyst)

基于 **通达信 mootdx + 腾讯财经 + DuckDB + AKShare 低频 + 开盘啦/同花顺热点层** 的 A 股多维度分析与选股系统。项目目标不是依赖单一爬虫源，而是把行情、估值、低频资料、热点情绪拆层治理，形成稳定可回测、可缓存、可扩展的数据底座。

## 核心数据源结论

实测后数据源取舍：

- ❌ 淘汰：`Tushare`、`Ashare`，不作为主链路。
- ✅ 行情主源：通达信 `mootdx`，负责实时行情和 K 线。
- ✅ 估值补字段：腾讯财经 API，负责 PE/PB、市值、换手率、量比。
- ✅ 低频补充：AKShare，仅用于股票列表 fallback、公告、研报、F10、财务摘要等低频数据。
- ✅ 热点情绪：同花顺热点/开盘啦，当前已落地开盘啦 App API 入库。
- ✅ 语义搜索：i问财，作为后续自然语言查询和事件驱动搜索扩展。

完整方案见：`docs/DATA_SOURCE_ARCHITECTURE.md`。

## 功能特性

- 📊 **行情/K线**：通达信主源 + DuckDB 本地缓存，支持日线和分钟线。
- 🧾 **估值补字段**：腾讯财经补充 PE/PB、市值、换手率、量比等。
- 🔥 **热点情绪**：开盘啦涨停原因、热点板块、涨停个股、市场情绪入库。
- 📈 **技术分析**：MA/EMA/MACD/RSI/KDJ/BOLL/ATR + 支撑压力位。
- 🏢 **基本面**：PE/PB/ROE/毛利率/净利率 + 估值评级。
- 🧠 **策略体系**：v1 / v2 / v3 / v3.1 / v3.2 / attack-third-buy 多版本策略。
- 💾 **本地缓存**：DuckDB 持久化股票池、K 线、开盘啦热点/情绪数据。

## 当前项目结构

```text
a-stock-analyst/
├── SOUL.md
├── run.py
├── requirements.txt
├── .env.example
├── app/
│   ├── core/
│   │   ├── config.py                    # 环境变量与数据源配置
│   │   ├── data_provider.py             # Web/策略统一门面
│   │   ├── providers/
│   │   │   ├── base.py                  # provider 抽象
│   │   │   ├── composite_provider.py    # 多源路由：DuckDB/mootdx/tencent/akshare
│   │   │   ├── mootdx_provider.py       # 通达信行情/K线主源
│   │   │   ├── tencent_provider.py      # 腾讯财经估值补字段
│   │   │   ├── akshare_provider.py      # AKShare 低频 fallback
│   │   │   ├── bigamap_provider.py      # BigAmap 公开热点补充
│   │   │   └── kaipanla_provider.py     # 开盘啦热点/情绪入库
│   │   └── storage/
│   │       └── duckdb_store.py          # DuckDB 表与 upsert/query
│   ├── analysis/                        # 技术/基本面/资金/综合/CZSC 分析
│   └── web/templates/                   # Flask 页面模板
├── scripts/
│   ├── sync_stock_list.py               # 股票池同步
│   ├── sync_history_kline.py            # 日线批量补库
│   └── sync_kaipanla_data.py            # 开盘啦热点/情绪同步
├── backtest/
│   ├── strategies/                      # 策略本体
│   ├── runners/                         # 可执行入口
│   ├── engine/                          # 回测引擎
│   └── archive/                         # 历史实验脚本
├── docs/
│   ├── DATA_SOURCE_ARCHITECTURE.md      # 数据源总方案
│   ├── KAIPANLA_DATA_SOURCE.md          # 开盘啦接入细节
│   ├── STRATEGY_ATTACK_THIRD_BUY.md
│   ├── STRATEGY_V3_1.md
│   └── STRATEGY_V3_2.md
└── data/ashare.duckdb                   # 本地缓存，不提交 Git
```

## 快速开始

### 1. 安装依赖

```bash
cd /Users/zhangzc/.Hermes/workspace/a-stock-analyst
uv pip install -r requirements.txt
```

### 2. 配置环境

```bash
cp .env.example .env
```

关键配置：

```env
TDX_HOST=127.0.0.1
TDX_PORT=7709
TENCENT_API=https://qt.gtimg.cn
AKSHARE_RATE_LIMIT=5
HOTSPOT_SOURCE=kaipanla
DUCKDB_PATH=data/ashare.duckdb
```

### 3. 初始化数据

同步股票池：

```bash
PYTHONPATH=. uv run python scripts/sync_stock_list.py
```

补日线缓存：

```bash
PYTHONPATH=. uv run python scripts/sync_history_kline.py --from-db-stock-list --limit 100 --start-date 20200101 --skip-if-exists
```

同步开盘啦热点/情绪：

```bash
PYTHONPATH=. uv run python scripts/sync_kaipanla_data.py --recent-days 1
```

### 4. 启动 Web 服务

```bash
DEBUG=false PYTHONPATH=. uv run python run.py
```

访问：

- 首页：`http://localhost:8888`
- 个股分析：`http://localhost:8888/stock/600519`

## 策略入口

### v3.1 主实时策略

```bash
PYTHONPATH=. uv run python -m backtest.runners.run_select_v3_1_top500
```

### attack-third-buy：热点 + 30 分钟三买

使用本地开盘啦热点池：

```bash
PYTHONPATH=. uv run python backtest/runners/run_select_attack_third_buy.py \
  --pool-mode kaipanla \
  --structure-period 30 \
  --max-stocks 80
```

综合热点池（开盘啦 + BigAmap）：

```bash
PYTHONPATH=. uv run python backtest/runners/run_select_attack_third_buy.py \
  --pool-mode combined \
  --structure-period 30 \
  --max-stocks 100
```

### v3.1 回测

```bash
PYTHONPATH=. uv run python backtest/runners/run_v3_1_backtest.py --codes 600449 --start-year 2019 --hold-weeks 10
PYTHONPATH=. uv run python backtest/runners/run_v3_1_backtest.py --stock-file backtest/stocks/fallback_stocks_v3_1.json --sample-size 20 --start-year 2019
```

### v3.2 排序版回测

```bash
PYTHONPATH=. uv run python backtest/runners/run_v3_2_backtest.py --codes 600449 601127 --stock-file backtest/stocks/fallback_stocks_v3_1.json --start-year 2019 --hold-weeks 10
```

## API 接口

- `GET /api/quote/{code}`：个股实时行情
- `GET /api/kline/{code}?period=daily|weekly|5|15|30|60`：K 线数据
- `GET /api/analysis/{code}`：全维度分析
- `GET /api/market`：市场概览
- `GET /api/sectors`：板块排行
- `GET /api/czsc/{code}`：缠论分析
- `GET /api/watchlist`：自选股 / 回测候选

## 采集频率原则

| 数据源 | 推荐频率 | 说明 |
|---|---:|---|
| 通达信 `mootdx` | 实时 / 1 分钟 | 行情主源 |
| 腾讯财经 | 5 分钟 | 估值补字段 |
| 开盘啦/同花顺热点 | 5-10 分钟盘中，收盘后补一次 | 热点归因 |
| AKShare | ≤5 次/分钟 | 只做低频 |
| i问财 | ≤2 次/分钟 | 待实现，防验证码 |

## 避坑要点

- 不要用 AKShare 高频爬实时行情。
- 不要多线程/分布式打东财接口。
- 策略脚本不要绕过 `data_provider.py` 或 provider 层直接访问行情源。
- DuckDB 是本地缓存核心；大批量回测/验证可用 `ASHARE_DUCKDB_READ_ONLY=1` 避免写锁冲突。
- `data/ashare.duckdb`、回测结果和缓存目录不提交 Git。

## 免责声明

本系统仅供学习研究使用，不构成投资建议。股市有风险，投资需谨慎；所有策略输出都需要人工复核。 
