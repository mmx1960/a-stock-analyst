# 开盘啦数据源接入

开盘啦是本项目“热点/情绪层”的当前落地实现，用于补足通达信/腾讯行情层无法提供的涨停原因、热点板块、强势股归因和市场情绪数据。完整数据源总架构见 `docs/DATA_SOURCE_ARCHITECTURE.md`。

接口调用方式参考 `jinhao2003/kaipanla-crawler`，但工程实现为项目内轻量 provider，不直接 vendoring 外部仓库。后续若接入真正的同花顺热点或 i问财，本模块仍作为热点层的一部分，而不是行情主源。

## 数据链路

```text
开盘啦 App API
  -> app/core/providers/kaipanla_provider.py
  -> app/core/storage/duckdb_store.py
  -> data/ashare.duckdb
  -> attack-third-buy 热点股票池
```

## DuckDB 表

- `kaipanla_market_sentiment`：每日市场情绪、涨跌停数量、连板分布、大盘指数。
- `kaipanla_limit_up_sectors`：涨停原因板块及板块涨停数量。
- `kaipanla_limit_up_stocks`：板块内涨停股票、连板天数、封单额、主题、涨停原因。
- `kaipanla_limit_up_ladder`：全市场连板梯队、反包板、打开高度标记。

所有表都保留 `raw_json`，后续字段解释变化时可回溯原始响应。

## 手动同步

同步今天数据：

```bash
PYTHONPATH=. uv run python scripts/sync_kaipanla_data.py --recent-days 1
```

同步最近 5 个交易日（跳过周末）：

```bash
PYTHONPATH=. uv run python scripts/sync_kaipanla_data.py --recent-days 5
```

同步指定日期：

```bash
PYTHONPATH=. uv run python scripts/sync_kaipanla_data.py --dates 2026-06-19 2026-06-20
```

输出示例：

```text
[1/1] 2026-06-19 market=1 sectors=12 stocks=58 ladder=58
```

## 定时任务建议

开盘啦数据适合盘中和收盘后增量刷新：

```cron
# 盘中每 10 分钟刷新一次热点/涨停数据
*/10 9-15 * * 1-5 cd /Users/zhangzc/.Hermes/workspace/a-stock-analyst && PYTHONPATH=. uv run python scripts/sync_kaipanla_data.py --recent-days 1

# 收盘后补一遍完整数据
10 15 * * 1-5 cd /Users/zhangzc/.Hermes/workspace/a-stock-analyst && PYTHONPATH=. uv run python scripts/sync_kaipanla_data.py --recent-days 1
```

如果用 Hermes cronjob，也应执行同一条脚本命令；脚本 stdout 会给出入库行数。

## 选股集成

`attack-third-buy` 已支持从本地开盘啦缓存构建热点池：

```bash
PYTHONPATH=. uv run python backtest/runners/run_select_attack_third_buy.py \
  --pool-mode kaipanla \
  --structure-period 30 \
  --max-stocks 80
```

默认 `combined` 模式会同时使用：

1. 本地开盘啦缓存热点股
2. BigAmap 涨停复盘
3. BigAmap 反复热点板块扩展股票池

## 验收标准

1. `scripts/sync_kaipanla_data.py` 能输出非异常结果。
2. DuckDB 中 `kaipanla_limit_up_stocks` 有记录。
3. `run_select_attack_third_buy.py --pool-mode kaipanla` 能读取本地热点池并继续走 30 分钟三买筛选。

注意：非交易日或开盘啦接口无当日数据时，`stocks=0` 不代表链路失败；应指定最近一个实际交易日复测。
