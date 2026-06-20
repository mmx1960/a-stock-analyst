# A 股分析系统 (A-Share Analyst)

基于 AKShare + Flask 的 A 股多维度分析系统，提供技术面、基本面、资金面综合分析，并沉淀多版本 A 股选股策略与回测脚本。

## 功能特性

- 📊 **实时行情**：A 股实时报价、指数行情、板块排行
- 📈 **技术分析**：MA/EMA/MACD/RSI/KDJ/BOLL/ATR + 支撑压力位
- 🏢 **基本面**：PE/PB/ROE/毛利率/净利率 + 估值评级
- 💰 **资金流向**：主力/超大单/大单/中单/散户资金流向
- 🎯 **综合评分**：100 分制加权评分（技术 40% + 基本面 35% + 资金面 25%）
- 🤖 **操作建议**：自动生成 BUY/HOLD/SELL 建议 + 置信度
- 📋 **板块监控**：行业板块涨跌排行、成交额统计
- 🧠 **策略体系**：v1 / v2 / v3 / v3.1 / v4 选股策略与回测脚本

## 技术架构

```text
a-stock-analyst/
├── SOUL.md                      # Agent 人格定义
├── run.py                       # 启动入口
├── requirements.txt             # 依赖
├── docs/
│   └── STRATEGY_V3_1.md         # 当前主实时选股策略文档
├── app/
│   ├── core/
│   │   ├── config.py            # 配置
│   │   └── data_provider.py     # 数据层（AKShare 封装）
│   ├── analysis/
│   │   ├── technical.py         # 技术分析
│   │   ├── fundamental.py       # 基本面分析
│   │   ├── capital_flow.py      # 资金流分析
│   │   ├── composite.py         # 综合评分
│   │   └── czsc_analyzer.py     # CZSC 缠论分析
│   └── web/
│       ├── templates/           # 页面模板
│       └── static/              # 静态资源
├── backtest/
│   ├── engine/
│   │   ├── backtest_engine.py   # 回测引擎
│   │   └── analyze_stock.py     # 子进程单股分析
│   ├── strategies/
│   │   ├── strategy_v1_triple_resonance.py     # v1 三级别共振
│   │   ├── strategy_v2_price_action.py         # v2 价格形态快筛
│   │   ├── strategy_v3_standard_buy_point.py   # v3 标准缠论买点
│   │   ├── strategy_v3_1_realtime.py           # v3.1 主实时策略
│   │   ├── strategy_v3_1_backtest.py           # v3.1 回测（复用主策略核心函数）
│   │   ├── strategy_v3_2_ranked.py             # v3.2 排序版策略（信号分级 + 打分）
│   │   └── strategy_v4_relaxed_fallback.py     # v4 放宽容错版
│   ├── runners/
│   │   ├── run_select_v3_1_top500.py           # 运行 v3.1 实时选股
│   │   ├── run_backtest_v3.py                  # 运行 v3 标准回测
│   │   ├── run_backtest_v3_1.py                # 旧版 v3.1 回测入口（全市场优先）
│   │   ├── run_backtest_v3_1_fallback.py       # 旧版 v3.1 回测入口（固定兜底样本）
│   │   ├── run_v3_1_backtest.py                # 正式 v3.1 回测入口（单票/股票池/自动 fallback）
│   │   ├── run_v3_2_backtest.py                # 正式 v3.2 回测入口（排序/优先级输出）
│   ├── archive/                                # 历史实验脚本归档
│   └── results_v6/                             # 回测结果
└── current_v3_selections.json                  # 最新实时选股结果
```

## 策略体系

- **v1：三级别共振策略**
  - 文件：`backtest/strategies/strategy_v1_triple_resonance.py`
- **v2：价格形态快筛策略**
  - 文件：`backtest/strategies/strategy_v2_price_action.py`
- **v3：标准缠论买点策略（主回测基线）**
  - 文件：`backtest/strategies/strategy_v3_standard_buy_point.py`
- **v3.1：当前主实时选股策略**
  - 文档：`docs/STRATEGY_V3_1.md`
  - 策略：`backtest/strategies/strategy_v3_1_realtime.py`
  - 核心函数：`analyze_v3_1_signal(...)`
  - 回测：`backtest/strategies/strategy_v3_1_backtest.py`
  - 实时入口：`backtest/runners/run_select_v3_1_top500.py`
  - 正式回测入口：`backtest/runners/run_v3_1_backtest.py`
  - 兼容旧入口：`backtest/runners/run_backtest_v3_1.py`
  - 固定样本入口：`backtest/runners/run_backtest_v3_1_fallback.py`
- **v3.2：排序版策略（推荐作为下一阶段主策略）**
  - 策略：`backtest/strategies/strategy_v3_2_ranked.py`
  - 核心函数：`analyze_v3_2_signal(...)`
  - 回测入口：`backtest/runners/run_v3_2_backtest.py`
  - 主要增强：信号优先级（P1/P2/P3）+ `signal_score` 排序输出
- **v4：放宽规则的容错补充策略**
  - 文件：`backtest/strategies/strategy_v4_relaxed_fallback.py`

## 快速开始

### 1. 安装依赖

```bash
cd ~/.Hermes/workspace/a-stock-analyst
pip install -r requirements.txt
```

### 2. 启动 Web 服务

```bash
python run.py
```

### 3. 运行主实时选股策略（v3.1）

```bash
python -m backtest.runners.run_select_v3_1_top500
```

### 4. 运行 v3.1 回测

```bash
# 单票回测
python backtest/runners/run_v3_1_backtest.py --codes 600449 --start-year 2019 --hold-weeks 10

# 本地股票池回测
python backtest/runners/run_v3_1_backtest.py --stock-file backtest/stocks/fallback_stocks_v3_1.json --sample-size 20 --start-year 2019

# 自动 fallback 回测（优先实时股票列表，失败时回退到默认股票池）
python backtest/runners/run_v3_1_backtest.py --sample-size 100 --start-year 2020
```

### 5. 运行 v3.2 排序版回测

```bash
# 单票/多票排序回测
python backtest/runners/run_v3_2_backtest.py --codes 600449 601127 --stock-file backtest/stocks/fallback_stocks_v3_1.json --start-year 2019 --hold-weeks 10

# 股票池排序回测
python backtest/runners/run_v3_2_backtest.py --stock-file backtest/stocks/fallback_stocks_v3_1.json --sample-size 18 --start-year 2019
```

### 6. 运行 v3 标准回测

```bash
python -m backtest.runners.run_backtest_v3
```

### 7. 访问

- 首页：http://localhost:8888
- 个股分析：http://localhost:8888/stock/600519

## API 接口

- `GET /api/quote/{code}`：个股实时行情
- `GET /api/kline/{code}`：K 线数据
- `GET /api/analysis/{code}`：全维度分析
- `GET /api/market`：市场概览
- `GET /api/sectors`：板块排行
- `GET /api/czsc/{code}`：缠论分析
- `GET /api/watchlist`：自选股 / 回测候选

## 数据源

- **AKShare**：东方财富 / 新浪 / 腾讯等多源数据，免费无需 API key
- 自动故障切换 + 请求限流 + 内存缓存

## 注意事项

- 本系统仅供学习研究使用，不构成投资建议
- 股市有风险，投资需谨慎
- AI 生成内容可能存在错误，请自行验证
