# A股多源数据架构迁移方案 Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 将项目从“AKShare 为主的单体数据层”升级为“mootdx 主行情 + 腾讯财经补估值 + DuckDB 本地历史库 + AKShare/i问财/同花顺补充”的多源稳定数据架构。

**Architecture:** 新增 provider 抽象层、DuckDB 本地历史行情仓库和数据同步脚本；Web 与回测统一走 provider 接口，不再直接依赖 AKShare 行情接口。历史 K 线采用“全量拉取到 DuckDB，本地查询优先”，实时行情采用“mootdx 主、腾讯财经补字段、AKShare 低频兜底”的组合式聚合。

**Tech Stack:** Python, pandas, requests, duckdb, mootdx, Flask, existing a-stock-analyst codebase

---

## 当前现状（已勘察）

- Web 数据入口：`app/core/data_provider.py`
- Web 路由入口：`run.py`
- 回测历史 K 线：`backtest/strategies/strategy_v3_1_backtest.py`
- 当前方案问题：
  - 行情与 K 线主依赖 AKShare / 东财链路
  - Web 和回测各自维护 fallback，存在重复逻辑
  - 本地历史缓存主要是 CSV cache，不是统一数据库
  - 没有 provider 抽象，策略层直接感知数据源细节

---

## 目标架构

```text
a-stock-analyst/
├── app/
│   └── core/
│       ├── config.py
│       ├── data_provider.py                 # 保留门面，但改为组合式 provider
│       ├── providers/
│       │   ├── __init__.py
│       │   ├── base.py                      # provider 协议 / schema
│       │   ├── mootdx_provider.py           # 实时 + K线主源
│       │   ├── tencent_provider.py          # 估值/补充行情
│       │   ├── akshare_provider.py          # 低频补充（公告/研报/股票列表兜底）
│       │   └── composite_provider.py        # 聚合多个 provider
│       └── storage/
│           ├── __init__.py
│           ├── duckdb_store.py              # DuckDB 读写封装
│           └── schema.sql                   # 表结构（可选）
├── scripts/
│   ├── sync_stock_list.py                   # 刷新股票列表
│   ├── sync_history_kline.py                # 全量/增量落库 DuckDB
│   └── healthcheck_data_sources.py          # 数据源连通性检查
└── backtest/
    └── ...                                  # 后续改为走统一历史数据接口
```

---

## 数据源职责划分

### 主链路
- **mootdx**
  - 股票列表（可选）
  - 实时价格 / 基础行情
  - 日线 / 周线 / 月线 / 分钟线
  - 历史 K 线全量同步主源

- **腾讯财经**
  - PE / PB / 总市值 / 流通市值 / 换手率 / 量比 等补充字段
  - 个股实时补充字段

### 低频补充链路
- **AKShare**
  - 公告 / 财报 / 研报 / F10 / 行业分类
  - 低频股票列表 fallback

### 后续扩展（本轮先不实现）
- **i问财**
  - 自然语言检索、舆情、资金流向
- **同花顺热点**
  - 热点归因、涨停原因、板块热度

---

## DuckDB 存储设计

### 表 1：`stock_basic`
字段建议：
- `code` TEXT PRIMARY KEY
- `name` TEXT
- `market` TEXT
- `exchange` TEXT
- `list_date` DATE
- `status` TEXT
- `updated_at` TIMESTAMP
- `source` TEXT

### 表 2：`daily_kline`
字段建议：
- `code` TEXT
- `trade_date` DATE
- `open` DOUBLE
- `high` DOUBLE
- `low` DOUBLE
- `close` DOUBLE
- `volume` DOUBLE
- `amount` DOUBLE
- `turnover_rate` DOUBLE
- `change_pct` DOUBLE
- `adjust` TEXT      # raw / qfq / hfq
- `source` TEXT
- `updated_at` TIMESTAMP
- PRIMARY KEY (`code`, `trade_date`, `adjust`)

### 表 3：`minute_kline`
字段建议：
- `code` TEXT
- `period` TEXT      # 1m/5m/15m/30m/60m
- `trade_dt` TIMESTAMP
- `open` DOUBLE
- `high` DOUBLE
- `low` DOUBLE
- `close` DOUBLE
- `volume` DOUBLE
- `amount` DOUBLE
- `source` TEXT
- `updated_at` TIMESTAMP
- PRIMARY KEY (`code`, `period`, `trade_dt`)

### 表 4：`realtime_quote_snapshot`
字段建议：
- `code` TEXT
- `quote_time` TIMESTAMP
- `price` DOUBLE
- `change_pct` DOUBLE
- `volume` DOUBLE
- `amount` DOUBLE
- `turnover_rate` DOUBLE
- `pe` DOUBLE
- `pb` DOUBLE
- `market_cap` DOUBLE
- `circulating_cap` DOUBLE
- `volume_ratio` DOUBLE
- `source_main` TEXT
- `source_extra` TEXT

---

## 分阶段实施

### Phase 1：基础设施搭好
1. 新增配置项：mootdx、DuckDB、腾讯源相关 env/config
2. 加 `providers/` 目录和抽象接口
3. 加 `duckdb_store.py`
4. requirements 增加 `duckdb`、`mootdx`

### Phase 2：先打通 Web 数据层
1. `CompositeProvider` 提供：
   - `get_stock_list()`
   - `get_realtime_quote(code)`
   - `get_kline_daily(code, ...)`
   - `get_kline_minute(code, ...)`
2. `app/core/data_provider.py` 改为调用 composite provider
3. K 线优先走 DuckDB，本地 miss 时再走 mootdx 拉取并回填

### Phase 3：历史 K 线同步
1. `scripts/sync_stock_list.py`
2. `scripts/sync_history_kline.py`
3. 支持：
   - 全量初始化
   - 增量刷新最近 N 天
   - 指定股票重拉

### Phase 4：回测链路切换
1. `backtest/strategies/strategy_v3_1_backtest.py` 改为优先查 DuckDB
2. 数据缺口时走 mootdx 抓取并回填
3. 逐步去掉对 AKShare 历史 K 线的强依赖

### Phase 5：低频补充能力
1. 公告 / 研报 / F10 保留 AKShare
2. 后续再接 i问财 / 同花顺热点

---

## Bite-sized tasks

### Task 1: 扩展配置，声明多源数据架构开关

**Objective:** 增加 DuckDB、mootdx、腾讯财经配置项，让后续 provider 有统一配置来源。

**Files:**
- Modify: `app/core/config.py`
- Test: `python -c "from app.core.config import DATA_SOURCE_CONFIG; print(DATA_SOURCE_CONFIG)"`

**Step 1: Add config dicts**
新增：
- `DATA_SOURCE_CONFIG`
- `DUCKDB_PATH`
- `MOOTDX_CONFIG`
- `TENCENT_CONFIG`

**Step 2: Verify import**
Run:
```bash
python -c "from app.core.config import DATA_SOURCE_CONFIG, DUCKDB_PATH; print(DATA_SOURCE_CONFIG, DUCKDB_PATH)"
```
Expected: 打印配置，不报错。

---

### Task 2: 新建 provider 抽象接口

**Objective:** 把“数据源能力”统一成接口，避免上层直接依赖具体 SDK。

**Files:**
- Create: `app/core/providers/__init__.py`
- Create: `app/core/providers/base.py`
- Test: `python -c "from app.core.providers.base import BaseMarketDataProvider; print('OK')"`

**Step 1: Define protocol/base class**
接口至少包括：
- `get_stock_list()`
- `get_realtime_quote(code)`
- `get_daily_bars(code, start_date, end_date, adjust)`
- `get_minute_bars(code, period, start_date, end_date)`

**Step 2: Verify import**
Run:
```bash
python -c "from app.core.providers.base import BaseMarketDataProvider; print('OK')"
```
Expected: `OK`

---

### Task 3: 新建 DuckDB 存储层

**Objective:** 提供本地历史行情数据库的最小读写封装。

**Files:**
- Create: `app/core/storage/__init__.py`
- Create: `app/core/storage/duckdb_store.py`
- Test: `python -c "from app.core.storage.duckdb_store import DuckDBStore; s=DuckDBStore(); print(s.db_path)"`

**Step 1: Implement DB bootstrap**
功能包括：
- 自动创建数据库文件
- 自动建表 `stock_basic` / `daily_kline` / `minute_kline`

**Step 2: Implement minimal methods**
- `upsert_stock_basic(df)`
- `upsert_daily_kline(df)`
- `get_daily_kline(code, start_date, end_date, adjust)`
- `has_daily_kline(code, start_date, end_date, adjust)`

**Step 3: Verify bootstrap**
Run:
```bash
python -c "from app.core.storage.duckdb_store import DuckDBStore; s=DuckDBStore(); print(s.db_path.exists())"
```
Expected: `True`

---

### Task 4: 新建 MootdxProvider 骨架

**Objective:** 用 mootdx 作为主行情/K线 provider 的最小可运行骨架。

**Files:**
- Create: `app/core/providers/mootdx_provider.py`
- Modify: `requirements.txt`
- Test: `python -c "from app.core.providers.mootdx_provider import MootdxProvider; print('OK')"`

**Step 1: Add dependency**
`requirements.txt` 增加：
- `duckdb>=1.0`
- `mootdx>=0.11`

**Step 2: Implement skeleton only**
先实现：
- 初始化配置
- symbol 规范化
- 空实现/占位抛错改为 `NotImplementedError` 或返回空 DataFrame
- 不在这一 task 强行写全逻辑

**Step 3: Verify import**
Run:
```bash
python -c "from app.core.providers.mootdx_provider import MootdxProvider; print('OK')"
```
Expected: `OK`

---

### Task 5: 新建 TencentProvider 骨架

**Objective:** 用腾讯财经做补充估值/实时字段 provider。

**Files:**
- Create: `app/core/providers/tencent_provider.py`
- Test: `python -c "from app.core.providers.tencent_provider import TencentProvider; print('OK')"`

**Step 1: Define scope clearly**
只负责：
- `get_realtime_quote_extra(code)`
- 不负责全量历史主抓取

**Step 2: Normalize output schema**
输出统一字段：
- `pe`
- `pb`
- `market_cap`
- `circulating_cap`
- `turnover_rate`
- `volume_ratio`

**Step 3: Verify import**
Run:
```bash
python -c "from app.core.providers.tencent_provider import TencentProvider; print('OK')"
```
Expected: `OK`

---

### Task 6: 新建 CompositeProvider

**Objective:** 聚合 mootdx、腾讯、AKShare fallback，并对上层暴露统一接口。

**Files:**
- Create: `app/core/providers/akshare_provider.py`
- Create: `app/core/providers/composite_provider.py`
- Test: `python -c "from app.core.providers.composite_provider import CompositeProvider; print('OK')"`

**Step 1: Wrap legacy AKShare logic**
把现有低频可复用能力迁到 `AkshareProvider`：
- 股票列表 fallback
- 公告/财报/F10 未来保留入口

**Step 2: Implement routing policy**
- `get_stock_list()`：mootdx → akshare fallback → local cache
- `get_realtime_quote(code)`：mootdx 主字段 + tencent 补字段
- `get_daily_bars(...)`：DuckDB → mootdx fetch/backfill → akshare fallback（只临时）
- `get_minute_bars(...)`：DuckDB / mootdx

**Step 3: Verify import**
Run:
```bash
python -c "from app.core.providers.composite_provider import CompositeProvider; print('OK')"
```
Expected: `OK`

---

### Task 7: 重构 app/core/data_provider.py 为门面层

**Objective:** 保持 `run.py` 和分析模块调用方式不变，但底层切到新 composite provider。

**Files:**
- Modify: `app/core/data_provider.py`
- Test: `python -c "from app.core.data_provider import data_provider; print(type(data_provider))"`

**Step 1: Preserve public API**
保留现有方法名：
- `get_stock_list`
- `get_realtime_quote`
- `get_kline_daily`
- `get_kline_minute`

**Step 2: Internally delegate**
内部改为调用 `CompositeProvider`

**Step 3: Verify import**
Run:
```bash
python -c "from app.core.data_provider import data_provider; print('OK')"
```
Expected: `OK`

---

### Task 8: 新建股票列表同步脚本

**Objective:** 提供可单独执行的股票列表刷新脚本。

**Files:**
- Create: `scripts/sync_stock_list.py`
- Test: `python scripts/sync_stock_list.py --limit 20`

**Step 1: Script behavior**
- 从 composite provider 拉股票列表
- 写入 DuckDB `stock_basic`
- 可选写本地 JSON cache

**Step 2: Verify run**
Run:
```bash
python scripts/sync_stock_list.py --limit 20
```
Expected: 成功写入若干条，不报错。

---

### Task 9: 新建历史 K 线全量/增量同步脚本

**Objective:** 提供本地 DuckDB 历史库构建入口。

**Files:**
- Create: `scripts/sync_history_kline.py`
- Test: `python scripts/sync_history_kline.py --codes 600519 000001 --start-date 20200101 --adjust hfq`

**Step 1: Script behavior**
支持参数：
- `--codes`
- `--stock-file`
- `--start-date`
- `--end-date`
- `--adjust`
- `--limit`
- `--incremental-days`

**Step 2: Verify run**
Run:
```bash
python scripts/sync_history_kline.py --codes 600519 000001 --start-date 20200101 --adjust hfq
```
Expected: DuckDB 有对应数据落盘。

---

### Task 10: 回测层优先改为 DuckDB 读数

**Objective:** 先把回测历史 K 线入口切到本地数据库优先，减轻线上源压力。

**Files:**
- Modify: `backtest/strategies/strategy_v3_1_backtest.py`
- Test: 现有小样本 runner

**Step 1: Replace history fetch path**
优先顺序：
- DuckDB
- mootdx 拉取 + 回填 DuckDB
- AKShare fallback（仅临时兜底）

**Step 2: Verify with small sample**
Run:
```bash
uv run python -m backtest.runners.run_v3_2_backtest --codes 600519 000001 --start-year 2020 --hold-weeks 10 --output backtest/results_v6/smoke_mootdx.json
```
Expected: 成功跑出结果，不报历史数据缺失。

---

### Task 11: Web 层验证

**Objective:** 确认 Flask 接口在新数据层下可正常工作。

**Files:**
- Modify if needed: `run.py`
- Test via curl

**Step 1: Start app**
Run:
```bash
python run.py
```

**Step 2: Verify APIs**
Run:
```bash
curl "http://localhost:8888/api/quote/600519"
curl "http://localhost:8888/api/kline/600519?period=daily"
```
Expected: 返回 JSON，字段齐全。

---

## 风险与避坑

1. **mootdx 复权能力不是现成银弹**
   - 若无法稳定提供 hfq，需先以 raw 落库，再研究复权处理链路
2. **腾讯财经只能做补字段，不要反客为主**
3. **AKShare 不能彻底删**
   - 公告/研报/F10 仍有价值
4. **不要一上来就全量回测切换**
   - 先做小样本 A/B 验证
5. **Web 与回测必须共用同一 provider 规范**
   - 禁止再次分叉两套逻辑

---

## 验收标准

- [ ] 项目存在 provider 抽象层
- [ ] 项目存在 DuckDB 历史行情仓库
- [ ] Web `data_provider` 已切到新架构
- [ ] 历史 K 线支持 DuckDB 优先
- [ ] 能跑小样本回测
- [ ] 能查实时行情
- [ ] AKShare 降级为低频补充，不再承担高频主行情

---

## 推荐执行顺序

1. Task 1-7：先搭框架与门面
2. Task 8-9：打通数据同步
3. Task 10：切回测
4. Task 11：验 Web

---

## 这轮先做什么最值

如果要我现在立刻开始写代码，最值的是先做：
- Task 1
- Task 2
- Task 3
- Task 4
- Task 5
- Task 6
- Task 7

也就是：**先把骨架搭起来，别急着一次性重写所有抓取逻辑。**
