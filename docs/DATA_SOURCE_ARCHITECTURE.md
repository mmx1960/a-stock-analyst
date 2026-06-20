# A 股稳定数据源完整方案

本文档是 `a-stock-analyst` 的数据源总设计，统一项目实际代码、部署配置和后续扩展方向。结论来自 7 类数据源实测：淘汰 Tushare / Ashare，把高频行情交给通达信和腾讯，把 AKShare 降级为低频，把热点/舆情交给同花顺热点与 i问财。

## 1. 核心结论

### 淘汰数据源

- `Tushare`：权限/积分/API key 约束重，不适合作为本项目免费稳定主源。
- `Ashare`：维护弱、接口稳定性不足，不作为主链路。
- `AKShare 高频行情`：不能再承担实时行情/全量高频 K 线主源，容易受东财接口、代理、限流影响。

### 保留主力组合

| 层级 | 主数据源 | 定位 | 当前项目状态 |
|---|---|---|---|
| 行情层 | 通达信 `mootdx` | 实时行情、日/周/月/分钟 K 线主源 | 已接入：`app/core/providers/mootdx_provider.py` |
| 估值补字段 | 腾讯财经 API | PE/PB、市值、换手率、量比等 | 已接入：`app/core/providers/tencent_provider.py` |
| 低频基础/公告/研报 | AKShare | 股票列表 fallback、公告/研报/F10/财务摘要 | 已接入低频 fallback：`app/core/providers/akshare_provider.py` |
| 热点/情绪 | 同花顺热点 / 开盘啦 | 涨停原因、强势股归因、板块热度、短线情绪 | 已接入开盘啦 App API：`app/core/providers/kaipanla_provider.py`；同花顺热点/i问财待扩展 |
| 自然语言搜索 | i问财 `iwencai` | 语义查询、舆情、资金流、事件驱动搜索 | 待实现 provider |
| 本地存储 | DuckDB | K 线、股票池、热点、情绪缓存 | 已接入：`app/core/storage/duckdb_store.py` |

最终组合：**通达信 + 腾讯财经 + AKShare 低频 + i问财 + 同花顺热点/开盘啦 + DuckDB**。

## 2. 分层架构

```text
调用方：Web / backtest / strategy runners / scripts
  -> app/core/data_provider.py                  # 对外门面
  -> app/core/providers/composite_provider.py   # 多源路由
      -> DuckDBStore                            # 本地缓存优先
      -> MootdxProvider                         # 高频行情主源
      -> TencentProvider                        # 估值/补字段
      -> AkshareProvider                        # 低频 fallback
      -> KaipanlaProvider                       # 热点/涨停/情绪缓存
      -> IwencaiProvider                        # 待实现：自然语言搜索
  -> data/ashare.duckdb                         # 本地持久化
```

### 2.1 行情层：通达信 + 腾讯财经

**通达信 `mootdx` 主责：**

- 实时价格
- 日/周/月 K 线
- 5/15/30/60 分钟 K 线
- 本地/服务器直连，无 API key，稳定性高

**腾讯财经主责：**

- `pe`
- `pb`
- `market_cap`
- `circulating_cap`
- `turnover_rate`
- `volume_ratio`
- `amplitude`

项目实现：

- 股票池：`CompositeProvider.get_stock_list()` 走 `DuckDB stock_basic -> mootdx -> akshare fallback`
- 日线：`CompositeProvider.get_daily_bars()` 走 `DuckDB -> mootdx -> akshare fallback -> DuckDB`
- 分钟线：`CompositeProvider.get_minute_bars()` 走 `DuckDB -> mootdx -> DuckDB`
- 实时行情：`mootdx 主字段 + 腾讯财经补字段`

### 2.2 公告/财报层：AKShare 低频

主源：巨潮资讯/东方财富等由 AKShare 封装的低频接口。

使用边界：

- 可以用于公告、财报 PDF、分红、解禁、股东决议、研报索引。
- 不应用于高频实时行情或无间隔全量行情抓取。
- 统一通过 provider 层加限流，不允许策略脚本直接高频调用 AKShare。

### 2.3 研报/新闻层：AKShare 低频 + 后续新闻 provider

推荐：

- 东方财富研报：低频调用，缓存到 DuckDB 或文件索引。
- 财联社/东财新闻：后续单独 provider，避免与行情链路混在一起。

### 2.4 情绪/热点层：同花顺热点 + 开盘啦 + i问财

当前已落地开盘啦 App API：

- `kaipanla_market_sentiment`：市场涨跌停、涨跌家数、连板分布。
- `kaipanla_limit_up_sectors`：涨停原因板块。
- `kaipanla_limit_up_stocks`：涨停个股、主题、原因、封单额。
- `kaipanla_limit_up_ladder`：预留连板梯队表；当前历史接口不稳定，默认不同步。

同花顺热点后续定位：

- 涨停原因
- 强势股归因
- 板块热度
- 龙虎榜/短线情绪

i问财后续定位：

- 自然语言查询，如“近 3 日北向增持最多的 10 只票”。
- 语义股票池构建。
- 舆情/资金流/事件驱动搜索。

### 2.5 基础数据层：AKShare 低频 + DuckDB 缓存

基础数据包括：

- 股票列表
- 行业分类
- F10
- 财务摘要
- 公告/研报索引

原则：**低频拉取、本地缓存、策略只读本地或统一门面，不直接打源站。**

## 3. 当前项目结构

```text
a-stock-analyst/
├── app/
│   ├── core/
│   │   ├── config.py                    # 环境变量与数据源配置
│   │   ├── data_provider.py             # Web/策略统一门面
│   │   ├── providers/
│   │   │   ├── base.py                  # Provider 抽象
│   │   │   ├── composite_provider.py    # 多源路由：DuckDB/mootdx/tencent/akshare
│   │   │   ├── mootdx_provider.py       # 通达信行情/K线主源
│   │   │   ├── tencent_provider.py      # 腾讯财经估值补字段
│   │   │   ├── akshare_provider.py      # AKShare 低频 fallback
│   │   │   ├── bigamap_provider.py      # BigAmap 公开热点补充
│   │   │   └── kaipanla_provider.py     # 开盘啦热点/情绪入库
│   │   └── storage/
│   │       └── duckdb_store.py          # DuckDB 表与 upsert/query
├── scripts/
│   ├── sync_stock_list.py               # 股票池同步
│   ├── sync_history_kline.py            # 日线批量补库
│   └── sync_kaipanla_data.py            # 开盘啦热点/情绪同步
├── backtest/
│   ├── strategies/                      # 策略本体
│   └── runners/                         # 可执行入口
├── docs/
│   ├── DATA_SOURCE_ARCHITECTURE.md      # 本文档
│   ├── KAIPANLA_DATA_SOURCE.md          # 开盘啦落地细节
│   └── STRATEGY_*.md                    # 策略文档
└── data/ashare.duckdb                   # 本地数据缓存，不提交 Git
```

## 4. 环境配置

`.env` 推荐配置：

```env
# Flask
SECRET_KEY=change-me-to-a-random-string
DEBUG=false
PORT=8888

# DuckDB
DUCKDB_PATH=data/ashare.duckdb
ASHARE_DUCKDB_READ_ONLY=0

# 通达信 mootdx
TDX_HOST=127.0.0.1
TDX_PORT=7709
TDX_BESTIP_TIMEOUT=3.0

# 腾讯财经
TENCENT_API=https://qt.gtimg.cn
TENCENT_TIMEOUT=8.0

# AKShare 低频
AKSHARE_RATE_LIMIT=5
LOW_FREQ_SOURCE=akshare

# i问财（待实现 provider 时使用）
IWENCAI_COOKIE=
IWENCAI_RATE_LIMIT=30

# 热点源
HOTSPOT_SOURCE=kaipanla
KAIPANLA_RATE_LIMIT=0.5

# AI 可选
OPENAI_API_KEY=
OPENAI_API_BASE=https://api.openai.com/v1
AI_MODEL=gpt-4
```

## 5. 部署步骤

### 5.1 安装依赖

```bash
cd /Users/zhangzc/.Hermes/workspace/a-stock-analyst
uv pip install -r requirements.txt
```

或使用系统 Python：

```bash
pip install -r requirements.txt
```

### 5.2 初始化本地数据

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

### 5.3 启动 Web

```bash
DEBUG=false PYTHONPATH=. uv run python run.py
```

访问：`http://localhost:8888`

### 5.4 运行进攻型三买

使用本地开盘啦热点池 + 30 分钟三买：

```bash
PYTHONPATH=. uv run python backtest/runners/run_select_attack_third_buy.py \
  --pool-mode kaipanla \
  --structure-period 30 \
  --max-stocks 80
```

综合热点池：

```bash
PYTHONPATH=. uv run python backtest/runners/run_select_attack_third_buy.py \
  --pool-mode combined \
  --structure-period 30 \
  --max-stocks 100
```

## 6. 定时采集策略

### 推荐频率

| 数据源 | 推荐频率 | 原因 |
|---|---:|---|
| 通达信 `mootdx` | 实时 / 1 分钟 | 不走 HTTP 爬虫，不容易封 IP |
| 腾讯财经 | 5 分钟 | 公开接口，补估值字段 |
| 开盘啦/同花顺热点 | 5-10 分钟盘中，收盘后补一次 | 热点归因和涨停数据盘中会变化 |
| AKShare | ≤5 次/分钟 | 防止东财等源站限流/封 IP |
| i问财 | ≤2 次/分钟 | 防验证码/风控 |

### 当前已创建 Hermes cronjob

`sync-kaipanla-data`：工作日 `9-15` 点每 10 分钟执行：

```bash
cd /Users/zhangzc/.Hermes/workspace/a-stock-analyst
PYTHONPATH=. uv run python scripts/sync_kaipanla_data.py --recent-days 1 --timeout 20 --min-interval 0.2
```

### 禁止事项

- 禁止用 AKShare 高频爬实时行情。
- 禁止多线程/分布式高频打东财接口。
- 禁止无间隔循环请求同一 HTTP 接口。
- 禁止策略脚本绕过 `data_provider` / provider 层直接访问外部行情源。

## 7. 稳定性保障

1. **双源冗余**：行情走 `mootdx + tencent`，任一挂了可降级。
2. **本地缓存**：K 线、股票池、热点情绪全部进 DuckDB。
3. **自动重试**：后续 provider 应统一增加 3 次重试 + 指数退避。
4. **只读模式**：回测/并发验证可用 `ASHARE_DUCKDB_READ_ONLY=1` 避免 DuckDB 写锁冲突。
5. **IP 隔离**：AKShare/i问财如需高频，应使用独立代理或降低频率。
6. **健康监控**：至少监控股票池数量、K 线命中率、热点同步行数、最近更新时间。

## 8. Claude Code / MCP 用法

本项目当前是本地 Python provider 架构，不依赖 Claude Code MCP 才能运行。若要给 Claude Code 暴露数据源，建议后续用项目内 FastAPI/MCP wrapper 暴露统一接口，而不是直接把第三方 HTTP 接口裸接给 Claude。

### 推荐 MCP 形态

```bash
claude mcp add astock --transport http http://localhost:8000/mcp
```

由本项目 MCP 服务统一路由：

- `/tdx`：通达信行情/K线
- `/tencent`：腾讯估值补字段
- `/akshare-lowfreq`：公告/研报/F10
- `/hotspot`：开盘啦/同花顺热点
- `/iwencai`：自然语言查询

### 不推荐直接配置

不建议直接：

```bash
claude mcp add tencent --transport http https://stock.finance.qq.com/api
claude mcp add akshare --transport http https://api.akshare.xyz
claude mcp add iwencai --transport http https://www.iwencai.com/api
```

原因：这些并不是标准 MCP server；应由项目自己的 wrapper 负责鉴权、限流、缓存、字段归一。

### 自然语言查询目标

后续 MCP wrapper 完成后，应支持：

- “拿 600519 最近 10 天日线 + PE/PB” → `mootdx + tencent`
- “查 000001 2025 年财报公告” → `akshare lowfreq`
- “今天涨停的 10 只票分别是什么原因” → `kaipanla / 同花顺热点`
- “近一周北向资金净流入最多的 5 只 A 股” → `iwencai`

## 9. 已知问题与后续 TODO

### 已知问题

- `kaipanla_provider.get_market_limit_up_ladder(date)` 的历史 ladder 接口当前可能返回 `1020 参数出错`，所以 `sync_kaipanla_data.py` 默认不同步 ladder，只同步可用的市场情绪、涨停板块、涨停个股。
- 当前 `kaipanla_limit_up_stocks.consecutive_days` 在某些返回页可能为 0，热点池默认不以连板天数硬过滤。

### TODO

1. 新增 `iwencai_provider.py`：cookie 配置、限流、语义查询、结果缓存。
2. 新增真正的同花顺热点 provider：若能拿到稳定接口，替代/补充开盘啦热点。
3. 给 `CompositeProvider` 增加统一 retry/backoff 工具。
4. 增加健康检查脚本：股票池行数、K 线覆盖率、热点最新日期、DuckDB 锁状态。
5. 增加 MCP/FastAPI wrapper，统一暴露给 Claude Code。
