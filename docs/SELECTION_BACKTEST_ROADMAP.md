# 选股 / 回测整体建设计划

本文档收口 `a-stock-analyst` 后续主线：先把数据层、多策略选股、评分、回测、优化闭环做成同一套工程体系。当前阶段明确：**先不做定时任务**，所有同步与选股先通过手动命令触发；定时任务只作为后续运维增强项。

## 1. 总目标

系统最终收敛成两条核心链路：

```text
选股链路：DB-first 数据层 -> 用户选择策略 -> 标准信号 -> 板块/资金/行情评分 -> 二次筛选 -> 候选池
回测链路：历史日期回放同一选股链路 -> 买点后 N 日评估 -> 参数优化 -> 反哺选股默认参数
```

关键原则：

1. **选股和回测共用同一套数据接口**：策略、评分、回测都只读 `MarketDataProvider`，不直接访问 `akshare/mootdx/kaipanla`。
2. **选股和回测共用同一套策略接口**：实时选股与历史回测都通过 strategy registry 调用策略，避免双轨漂移。
3. **策略只负责发现买点**：不在策略里做最终入选决策。
4. **评分只负责横向比较信号质量**：板块强度、资金强度、行情强度、主题热度统一在评分层处理。
5. **回测只读历史快照**：回测过程中不联网补数据，缺口记录为 `data_missing`。
6. **先手动、后自动**：当前不创建 cron；所有数据同步和选股命令先手动跑通、验证稳定后再考虑调度。

## 2. 当前已具备的地基

截至当前代码状态，以下模块已经有雏形或已落地：

| 模块 | 当前状态 | 主要文件 |
|---|---|---|
| DB 存储 | DuckDB 已承接股票池、K 线、板块、实时快照等核心数据 | `app/core/storage/duckdb_store.py` |
| DB-first Provider | 已出现 `MarketDataProvider` / `DuckDBMarketDataProvider` | `app/core/data/market_data_provider.py` |
| 多策略 registry | 已出现 `SelectionStrategy`，注册了 `attack_third_buy_30m/daily`、`daily_uptrend_30m_ma13_pullback` 与 `d_shen_trend_30m_pullback` | `backtest/strategies/registry.py` |
| 通用选股 workflow | 已出现 `run_selection_workflow(...)`，综合分已调成 D神资金一致性口径：形态 45% + 板块资金 45% + 主题/市场热度 10% | `backtest/workflows/selection_workflow.py` |
| 通用选股 CLI | 已出现 `run_stock_selection_workflow.py` | `backtest/runners/run_stock_selection_workflow.py` |
| 进攻型三买兼容入口 | 已出现旧入口兼容 workflow | `backtest/runners/run_attack_third_buy_workflow.py` |
| 板块强度评分 | 已接入 `kaipanla_sector_strength` 与股票板块归属匹配，但当前只覆盖 9 个板块，D神视角仍不够 | `backtest/strategies/kaipanla_sector_strength_score.py` |
| 股票-板块归属补库 | 已有同步脚本，当前约覆盖 4062 只股票；D神/杰哥视角要求继续补到接近全市场 | `scripts/sync_stock_sector_membership.py` |
| D神/杰哥数据源诊断 | 新增覆盖率诊断和补源编排入口，能识别板块资金、涨停生态、股票归属缺口 | `scripts/diagnose_trader_data_sources.py`, `scripts/sync_trader_data_sources.py` |
| 当日行情同步 | 已有手动同步脚本 | `scripts/sync_today_market_data.py` |

这些模块仍需继续整理接口边界、测试覆盖和文档说明，但方向正确。

## 3. 目标目录边界

推荐最终结构：

```text
app/core/data/
  market_data_provider.py          # 统一数据接口，选股/回测共用
app/core/storage/
  duckdb_store.py                  # 本地 DB 真源
scripts/
  init_market_data.py              # 全量历史初始化，后续新增
  sync_today_market_data.py         # 当日增量同步，当前手动触发
  sync_stock_sector_membership.py   # 股票-板块归属补库
  sync_kaipanla_data.py             # 开盘啦市场情绪/强度同步
backtest/strategies/
  registry.py                      # 多策略注册表
  strategy_attack_third_buy.py      # 进攻型三买策略插件
  strategy_daily_uptrend_ma13_pullback.py # 日线上涨趋势 + 30分钟回踩 MA13
backtest/scoring/
  ...                              # 后续把板块/行情/主题评分迁入此处
backtest/workflows/
  selection_workflow.py             # 策略 + 评分 + 二次筛选
backtest/evaluation/
  ...                              # 后续新增：买点后收益评估
backtest/optimization/
  ...                              # 后续新增：参数优化
backtest/runners/
  run_stock_selection_workflow.py   # 通用选股入口
  run_selection_backtest.py         # 后续新增：历史回放回测
  run_strategy_compare_backtest.py  # 后续新增：多策略对比
  optimize_*                        # 参数优化入口
```

## 4. 分阶段落地计划

### 阶段 1：固化数据层接口

目标：让选股、评分、回测都只认 `MarketDataProvider`。

任务：

1. 梳理 `MarketDataProvider` 方法签名，固定最小接口：
   - `get_stock_list()`
   - `get_daily_bars(code, start_date, end_date)`
   - `get_minute_bars(code, period, start_date, end_date)`
   - `get_realtime_quote(codes)`
   - `get_sector_membership(code)`
   - `get_sector_strength(start_date, end_date, sector_names=None)`
2. 明确 `DuckDBMarketDataProvider` 默认只读 DB，不在策略调用时联网补数。
3. 对实时选股需要的当日数据，通过手动脚本提前写入 DB。
4. 补数据覆盖率检查函数，输出股票池、日线、分钟线、板块归属、板块强度覆盖率。

验收：

```bash
PYTHONPATH=. uv run pytest tests/test_market_data_provider.py tests/test_duckdb_store_realtime_quote.py -q
PYTHONPATH=. uv run python scripts/sync_today_market_data.py --from-db-stock-list --limit 1 --throttle 0
```

### 阶段 1A：按 D神 / 杰哥视角补数据源

诊断结论：当前不是 K 线缺，而是资金生态数据缺。

已验证覆盖率：
- `stock_basic`：5316 只，OK。
- `daily_hfq`：5204 只，OK。
- `minute_30`：5200 只，OK。
- `kaipanla_market_sentiment`：605 个交易日，OK。
- `realtime_quote_snapshot`：4004 只，仍缺约 1200 只，多为远端空返回。
- `stock_sector_membership`：4062 只，有 1349 个板块/概念，但仍未接近全市场。
- `kaipanla_sector_strength`：450 个交易日但只有 9 个板块，D神视角严重不足。
- `kaipanla_limit_up_sectors/stocks`：只有 1 个交易日，杰哥视角严重不足。

D神视角：
- K 线够了，但“板块资金一致性”不够；如果只靠形态，D神会说“不好说”。
- 优先补 `kaipanla_sector_strength` 的板块池宽度，至少从 9 个扩到 40+ 个核心行业/概念。
- 同时补 `stock_sector_membership`，否则股票无法准确匹配到资金强板块。

杰哥视角：
- 最大缺口是涨停生态历史：全市场总龙头、板块龙头、活口、跟风狗都依赖涨停/连板/题材梯队。
- 仅有 1 天 `kaipanla_limit_up_stocks/sectors` 不够，必须接入可回溯的历史复盘源，或导入本地历史涨停复盘文件。

新增工具：
```bash
PYTHONPATH=. uv run python scripts/diagnose_trader_data_sources.py --output data/reports/trader_data_source_coverage.json
PYTHONPATH=. uv run python scripts/sync_trader_data_sources.py --all --dry-run
```

手动补源建议：
```bash
# 先补股票-板块归属，小批量验证后再放大
PYTHONPATH=. uv run python scripts/sync_trader_data_sources.py --sync-membership --start-date 2026-06-01 --end-date 2026-06-19 --limit-boards 20 --throttle 0.5

# 再扩板块强度历史，先从 config/trader_core_sector_pool.txt 核心池跑
PYTHONPATH=. uv run python scripts/sync_trader_data_sources.py --sync-sector-strength --start-date 2026-06-01 --end-date 2026-06-19 --limit-sectors 20 --throttle 0.1

# 最后补近期市场情绪/涨停生态；历史涨停生态仍需额外可回溯数据源
PYTHONPATH=. uv run python scripts/sync_trader_data_sources.py --sync-kaipanla-recent --recent-days 20 --throttle 0.5
```

验收：
```bash
PYTHONPATH=. uv run pytest tests/test_diagnose_trader_data_sources.py tests/test_kaipanla_sector_strength_score.py tests/test_kaipanla_provider.py -q
```

### 阶段 2：固化多策略 registry

目标：选股策略不再写死为 30 分钟三买，用户可选择单策略或多策略组合。

任务：

1. 固化 `SelectionStrategy` 字段：
   - `strategy_id`
   - `strategy_name`
   - `description`
   - `run(...)`
2. 固化标准信号 schema：
   - `strategy_id`
   - `strategy_name`
   - `code`
   - `name`
   - `buy_date`
   - `signal_price`
   - `signal_score`
   - `theme`
   - `structure_period`
   - `signal_reason`
   - `strategy_metadata`
3. 当前至少保留：
   - `attack_third_buy_30m`
   - `attack_third_buy_daily`
   - `daily_uptrend_30m_ma13_pullback`
4. `run_stock_selection_workflow.py` 支持：
   - `--list-strategies`
   - `--strategy`
   - `--strategies`
   - `--dedupe-by`
   - `--merge-mode`

后续策略候选：

| strategy_id | 定位 |
|---|---|
| `attack_third_buy_30m` | 当前主策略，30 分钟进攻型三买 |
| `attack_third_buy_daily` | 日线三买对照/回退 |
| `daily_uptrend_30m_ma13_pullback` | 日线上涨趋势，30分钟线回踩并收回 MA13 买入；已跑通真实 workflow/backtest/optimizer 链路 |
| `v3_1_standard_buy_point` | 标准缠论买点 |
| `volume_retrace_breakout` | 放量阳线 + 缩量回调 |
| `hot_theme_leader_pullback` | 热点板块龙头回调 |
| `platform_breakout` | 平台突破 |

验收：

```bash
PYTHONPATH=. uv run python backtest/runners/run_stock_selection_workflow.py --list-strategies
PYTHONPATH=. uv run pytest tests/test_strategy_attack_third_buy.py -q
```

### 阶段 3：固化通用选股 workflow

目标：生产选股统一走 `策略 -> 标准信号 -> 评分 -> selected/rejected`。

任务：

1. 固化 `SelectionWorkflowConfig`：
   - `strategies`
   - `max_stocks`
   - `sector_score_date`
   - `min_sector_score`
   - `min_final_score`
   - `top_n`
   - `weights`
2. 输出结构固定为：
   - `raw_signals`
   - `selected`
   - `rejected`
   - `counts`
   - `params_snapshot`
   - `data_coverage`
3. 每个信号必须输出：
   - `workflow_final_score`
   - `workflow_score_breakdown`
   - `workflow_reject_reasons`
4. 旧入口 `run_attack_third_buy_workflow.py` 只做兼容包装，内部调用通用 workflow。
5. 多策略组合时，支持按 `code,buy_date` 去重，并保留被合并信号来源。

建议默认权重：

```text
final_score = signal_score * 0.55 + sector_score * 0.35 + theme_heat_score * 0.10
```

建议默认阈值：

```text
min_sector_score = 65
min_final_score = 45
```

验收：

```bash
PYTHONPATH=. uv run python backtest/runners/run_stock_selection_workflow.py \
  --strategy attack_third_buy_30m \
  --max-stocks 1 \
  --throttle 0 \
  --output /tmp/stock_selection_workflow_smoke.json

PYTHONPATH=. uv run pytest tests/test_attack_third_buy_workflow.py -q
```

说明：`raw_signals=0` 不代表失败；只要 CLI 成功结束并写出合法 JSON，就说明链路可运行。

### 阶段 4：建设初始化与手动同步体系

目标：把外部数据源调用限制在脚本层，选股/回测只读 DB。

当前阶段不做定时任务，只保留手动命令。

#### 4.1 全量初始化脚本

后续新增：`scripts/init_market_data.py`

职责：

1. 初始化股票池。
2. 初始化交易日历。
3. 初始化日线 K 线。
4. 初始化必要分钟线。
5. 初始化股票-板块归属。
6. 初始化历史板块强度/资金强度。
7. 输出初始化报告与缺口清单。

建议参数：

```bash
PYTHONPATH=. uv run python scripts/init_market_data.py \
  --start-date 2020-01-01 \
  --end-date 2026-06-20 \
  --include stock,calendar,daily,minute,sector-membership,sector-strength \
  --resume \
  --skip-if-exists \
  --workers 2
```

#### 4.2 当日手动同步脚本

已有：`scripts/sync_today_market_data.py`

当前先手动触发：

```bash
PYTHONPATH=. uv run python scripts/sync_today_market_data.py \
  --include quote \
  --from-db-stock-list \
  --limit 500 \
  --throttle 0.05
```

后续再扩展：

- `--include minute`
- `--include sector-strength`
- `--include market-sentiment`
- `--codes`
- `--pool selected|hot|all`

#### 4.3 暂不做 cron

当前明确不做：

- 不创建 Hermes cronjob。
- 不创建系统 crontab。
- 不创建 launchd 定时任务。

后续等手动链路稳定后，再考虑盘中/盘后调度。

### 阶段 5：建设历史回放回测

目标：回测不再绕过 workflow，而是按历史日期重复调用同一套选股 workflow。

后续新增：`backtest/runners/run_selection_backtest.py`

流程：

```text
for trade_date in [start_date, end_date]:
  1. provider 切到历史日期视角
  2. 运行指定策略或策略组合
  3. 用 trade_date 及之前可见数据打分
  4. 生成当天 selected/rejected
  5. 对 selected 计算买点后 hold_days 表现
  6. 写入 detailed 与 summary
```

必须避免未来函数：

1. 板块评分只能使用 `buy_date` 当天及之前数据。
2. K 线结构只能使用 `buy_date` 已发生数据。
3. 买点后收益只能用于评估，不能参与筛选。
4. 参数优化只能读取回测结果，不允许反向污染历史信号生成。

输出字段：

- `strategy_id`
- `buy_date`
- `score_snapshot`
- `params_snapshot`
- `hold_days`
- `max_return_pct`
- `min_return_pct`
- `close_return_pct`
- `win`
- `hit_target`
- `hit_stop_loss`
- `risk_reward_ratio`
- `data_missing_reasons`

验收 smoke：

```bash
ASHARE_DUCKDB_READ_ONLY=1 PYTHONPATH=. uv run python backtest/runners/run_selection_backtest.py \
  --strategy attack_third_buy_30m \
  --start-date 2026-06-01 \
  --end-date 2026-06-18 \
  --hold-days 10 \
  --max-trade-days 2 \
  --output /tmp/selection_backtest_smoke.json
```

### 阶段 6：建设参数优化模块

目标：基于回测结果，输出不同风格的推荐参数，而不是只找一个“最优”。

后续新增：`backtest/optimization/` 与 runner。

参数搜索范围初版：

```text
min_sector_score: 45 / 55 / 65 / 75
min_final_score: 40 / 45 / 50 / 55
sector_weight: 0.25 / 0.35 / 0.45
signal_weight: 0.45 / 0.55 / 0.65
hold_days: 5 / 10 / 20
```

输出三类推荐：

1. 高胜率型。
2. 高盈亏比型。
3. 平衡型。

每组参数必须输出：

- 信号数
- 覆盖率
- 胜率
- 平均收益
- 中位收益
- 最大回撤
- 盈亏比
- 样本量是否达标

优化规则：

1. 按 `strategy_id` 分组优化，不把多策略混在一起。
2. 有样本量下限，避免过拟合。
3. 优化结果只反哺默认参数，不直接改历史回测数据。

### 阶段 7：补文档与验收命令

最终需要补齐：

1. `README.md`：说明当前主入口、数据初始化、手动同步、选股、回测。
2. `docs/DATA_SOURCE_ARCHITECTURE.md`：同步最新 DB-first 数据层边界。
3. `docs/STRATEGY_ATTACK_THIRD_BUY.md`：明确它只是策略插件之一。
4. `docs/SELECTION_BACKTEST_ROADMAP.md`：本文档持续维护。

最终验收命令：

```bash
PYTHONPATH=. uv run pytest \
  tests/test_market_data_provider.py \
  tests/test_duckdb_store_realtime_quote.py \
  tests/test_strategy_attack_third_buy.py \
  tests/test_attack_third_buy_workflow.py \
  tests/test_kaipanla_sector_strength_score.py \
  -q

PYTHONPATH=. uv run python backtest/runners/run_stock_selection_workflow.py --list-strategies

PYTHONPATH=. uv run python backtest/runners/run_stock_selection_workflow.py \
  --strategy attack_third_buy_30m \
  --max-stocks 1 \
  --throttle 0 \
  --output /tmp/stock_selection_workflow_smoke.json
```

## 5. 当前阶段完成状态

不做定时任务的前提下，当前最小闭环已经落地：

1. `scripts/init_market_data.py`：统一初始化入口，串联股票池、日线、股票-板块归属、板块强度四类手动补库。
2. `backtest/strategies/strategy_daily_uptrend_ma13_pullback.py`：新增真实策略插件，规则为日线 MA20>MA60 且趋势向上，30分钟线回踩并收回 MA13 买入。
3. `backtest/workflows/selection_workflow.py`：新增 `as_of_date` 回放日期透传，避免历史回测使用当前最新数据。
4. `backtest/runners/run_selection_backtest.py`：历史回放回测入口，按交易日调用同一套 selection workflow，并用 DB 日线评估持有期收益。
5. `backtest/evaluation/hold_return.py`：买点后固定持有窗口评估器。
6. `backtest/runners/optimize_selection_thresholds.py`：基于 selection backtest JSON 的阈值候选优化器。
7. `backtest/optimization/threshold_optimizer.py`：按 `strategy_id` 分组评估 `min_sector_score / min_final_score` 候选参数。
8. `backtest/runners/run_strategy_pipeline.py`：一键串联选股 workflow、历史回放回测、阈值优化和数据覆盖率快照。

已验证的最小命令：

```bash
PYTHONPATH=. uv run pytest tests/test_strategy_pipeline_runner.py tests/test_strategy_daily_uptrend_ma13_pullback.py tests/test_selection_backtest_runner.py tests/test_selection_threshold_optimizer.py tests/test_attack_third_buy_workflow.py tests/test_market_data_provider.py -q
ASHARE_DUCKDB_READ_ONLY=1 PYTHONPATH=. uv run python backtest/runners/run_strategy_pipeline.py --strategy daily_uptrend_30m_ma13_pullback --start-date 2026-05-22 --end-date 2026-05-22 --hold-days 10 --max-trade-days 1 --max-stocks 500 --signal-window-days 400 --min-sector-score 0 --min-final-score 0 --throttle 0 --sector-scores 0,20,40 --final-scores 0,40,60 --min-samples 1 --output-dir /tmp/strategy_pipeline_smoke
```

最新真实 pipeline 结果：一个命令产出 `selection_workflow.json`、`selection_backtest.json`、`threshold_optimization.json`、`data_coverage.json`、`pipeline_summary.json` 五个文件；`daily_uptrend_30m_ma13_pullback` 在 2026-04-20..2026-05-22 的 20 个交易日回放中产生 132 笔交易，118 笔完成持有期评估，`data_missing=14`。全样本 10 日持有 `win_rate=87.29`、`hit_target_rate=13.56`、`avg_max_return_pct=5.6555`、`avg_close_return_pct=-2.678`；优化器推荐 `min_sector_score=40`、`min_final_score=0`，该子样本 96 笔信号、82 笔评估、`hit_target_rate=15.85`、`avg_close_return_pct=-1.9434`。数据覆盖率快照显示：股票池 5316、日线 hfq 5204、30分钟线 5200、板块归属 4062、板块强度 50 个核心板块、实时快照 4004 条。

最新补库结果：`scripts/sync_history_kline.py` 已支持 `--period daily|5|15|30|60`；30 分钟线从 2017 只批量补到 5200 只，仅 5 只远端持续为空：`000004/001331/002808/688121/688287`。实时快照从 5 条扩到 4004 条，另有 1200 只远端返回空。开盘啦板块区间强度已扩到 50 个核心板块、18517 行、450 个交易日，范围 `2024-09-19..2026-06-19`。开盘啦涨停原因/个股历史接口实测会忽略历史日期，只返回近端 `2026-06-18`；因此新增 `scripts/sync_limit_up_history_sources.py`，先尝试 AKShare 东方财富涨停池近端数据，历史不可得时用本地 `daily_kline` 真实涨停价规则批量推导涨停股，再关联 `stock_sector_membership` 落到 `kaipanla_limit_up_stocks/sectors`。当前历史涨停生态已补到：`limit_up_stocks=114101` 行、`4570` 只、`585` 天；`limit_up_sectors=70140` 行、`745` 个板块、`585` 天，范围 `2024-01-02..2026-06-19`。

## 6. 下一步建议

下一步应从“最小闭环”升级到“有样本可解释”：

1. 补数据优先级：30分钟线已基本补齐到 5200 只，板块强度和历史涨停生态已达到可用；下一步优先补 `stock_sector_membership` 从 4062 只扩到 5000+，并排查实时快照 1200 只远端空的代码类型。
2. 在 `daily_uptrend_30m_ma13_pullback` 上追加风险过滤：排除 ST、加入成交额/流动性、日线乖离率、回踩后止损条件。
3. 扩大 pipeline smoke：从单日扩到 `5/20` 个交易日，先保证样本数，再评估参数。
4. 把评分逻辑从 `backtest/strategies/kaipanla_sector_strength_score.py` 逐步迁到 `backtest/scoring/`，让策略目录只保留策略。
5. 保持回测 `as_of_date` 截断约束：任何新策略都必须支持历史回放日期，不能用当前最新 K 线判断历史买点。

仍然不建议现在做：

1. 不建议马上创建 cron。
2. 不建议继续新增很多策略。
3. 不建议让策略绕过 provider 直接抓数据。
4. 不建议在回测里临时联网补历史数据。
5. 不建议在无样本优化结果前改生产默认阈值。

## 7. 手动操作备忘

当前阶段推荐手动执行顺序：

```bash
# 1. 小批量初始化 / 补库计划预览
PYTHONPATH=. uv run python scripts/init_market_data.py --include stock,daily,sector-membership,sector-strength --stock-limit 1 --daily-limit 1 --sector-membership-limit-history-rows 1 --sector-strength-limit-sectors 1 --start-date 2026-06-18 --end-date 2026-06-18 --dry-run

# 2. 小批量真实初始化
PYTHONPATH=. uv run python scripts/init_market_data.py --include stock,daily,sector-membership,sector-strength --stock-limit 1 --daily-limit 1 --sector-membership-limit-history-rows 1 --sector-strength-limit-sectors 1 --start-date 2026-06-18 --end-date 2026-06-18 --throttle 0 --continue-on-error

# 3. 查看策略列表
PYTHONPATH=. uv run python backtest/runners/run_stock_selection_workflow.py --list-strategies

# 4. 分批补 30 分钟线与实时快照（示例：先生成缺口批次，再同步）
PYTHONPATH=. uv run python - <<'PY'
import duckdb, pathlib
con=duckdb.connect('data/ashare.duckdb')
df=con.execute("""
select s.code from stock_basic s
join (select distinct code from daily_kline where adjust='hfq') d using(code)
left join (select distinct code from minute_kline where period='30') m using(code)
where m.code is null
order by s.code
limit 50
""").df()
path=pathlib.Path('/tmp/missing_30m_batch_50.txt')
path.write_text(' '.join(df['code'].astype(str).tolist()), encoding='utf-8')
print(path, len(df))
PY
PYTHONPATH=. uv run python scripts/sync_history_kline.py --codes $(cat /tmp/missing_30m_batch_50.txt) --period 30 --start-date 20260601 --end-date 20260618 --skip-if-exists
PYTHONPATH=. uv run python scripts/sync_today_market_data.py --codes $(cat /tmp/missing_30m_batch_50.txt) --throttle 0

# 5. 跑一键策略 pipeline smoke
ASHARE_DUCKDB_READ_ONLY=1 PYTHONPATH=. uv run python backtest/runners/run_strategy_pipeline.py --strategy daily_uptrend_30m_ma13_pullback --start-date 2026-05-22 --end-date 2026-05-22 --hold-days 10 --max-trade-days 1 --max-stocks 500 --signal-window-days 400 --min-sector-score 0 --min-final-score 0 --throttle 0 --sector-scores 0,20,40 --final-scores 0,40,60 --min-samples 1 --output-dir /tmp/strategy_pipeline_smoke

# 6. 跑核心测试
PYTHONPATH=. uv run pytest tests/test_strategy_pipeline_runner.py tests/test_strategy_daily_uptrend_ma13_pullback.py tests/test_selection_backtest_runner.py tests/test_selection_threshold_optimizer.py tests/test_attack_third_buy_workflow.py tests/test_market_data_provider.py -q
```
