# 进攻型三买选股策略 v1

> 项目：`a-stock-analyst`  
> 策略代号：`attack-third-buy-v1`  
> 目标：选出处在热点板块 / 资金主线中的进攻型主升趋势股票，而不是低位防守型反抽。

---

## 1. 策略定位

这套策略不替代 `v3.1` 的稳健回调买点，而是新增一条更进攻的候选池：

- `v3.1`：趋势背景 + 回调结束，偏稳健。
- `v3.2`：在 v3.1 基础上排序。
- `attack-third-buy-v1`：板块和资金热点先行，底层形态基于缠论三买，目标是主升趋势延续。

一句话：

> **先找市场正在攻击的方向，再在方向里找已经脱离中枢、回踩不破、重新转强的三买。**

---

## 2. 总体漏斗

```text
市场环境
  -> 热点板块 / 题材强度
  -> 板块内进攻核心股
  -> 个股主升趋势结构
  -> 缠论三买确认
  -> 量价 / 强度 / 风险过滤
  -> 排序输出
```

核心原则：

1. 不从全市场裸扫三买，先看板块和资金。
2. 不做长期弱势股的低位三买，只做主升结构里的三买。
3. 涨停梯队、板块排名、异动和封单强度是方向层；K 线三买是个股层。
4. 策略输出是“进攻候选”，不是自动买入指令。

---

## 3. 数据源设计

### 3.1 BigAmap / 开盘啦公开接口

已验证 BigAmap 前端公开接口：

- `GET https://bigamap.cn/api/v1/public/map/limit-up-review`
  - 涨停复盘：涨停、炸板、昨日涨停、强势股
  - 可提取：连板数、首次涨停时间、封单金额、炸板次数、行业/申万一级
- `GET https://bigamap.cn/api/v1/public/map/stock-abnormal`
  - 异动/严重异动监控
- `GET https://bigamap.cn/api/v1/public/map/boards/treemap`
  - 行业/板块树图，含板块层级与成分股涨跌
- `GET https://bigamap.cn/api/v1/public/map/boards/maximized-rankings`
  - 板块轮动排行，游客可见最近数日
- `GET https://bigamap.cn/api/v1/public/stocks/screener/filters/kaipanla-concepts?q=<关键词>`
  - 开盘啦概念词搜索

本项目已新增 provider：

- `app/core/providers/bigamap_provider.py`

### 3.2 本地行情链路

个股 K 线仍走现有统一门面：

- `app.core.data_provider.data_provider`
- DuckDB -> mootdx -> AKShare fallback
- 日线回测使用 `hfq`

---

## 4. 板块 / 题材热度评分

### 4.1 板块热度来源

优先级：

1. 涨停复盘行业聚合
2. 板块轮动 3 日 / 7 日排行
3. 板块树图涨跌与成分股涨幅扩散
4. 异动/严重异动预警
5. 开盘啦概念词扩展

### 4.2 热度分数 `theme_heat_score`

建议初版：

```text
limit_up_count_score      = min(30, 涨停数 * 6)
leader_height_score       = min(25, 最大连板数 * 8)
sealed_amount_score       = min(20, log10(封单金额合计 / 1e7 + 1) * 8)
one_word_score            = min(10, 一字涨停数 * 5)
rotation_rank_score       = 0~15，来自 3日/7日排行
abnormal_score            = 0~10，来自异动/严重异动监控
```

总分：

```text
theme_heat_score = 上述分数加总，封顶 100
```

初版硬条件：

- `theme_heat_score >= 60`
- 或板块内存在：
  - 最大连板数 `>= 2`
  - 且涨停数 `>= 2`

---

## 5. 个股候选池构建

候选来源：

1. 热点板块内所有成分股
2. 涨停复盘内强势股 / 昨日涨停 / 炸板修复股
3. 板块树图中当日涨幅靠前且成交活跃个股
4. 用户指定题材词通过开盘啦概念搜索扩展

初版排除：

- ST / 退市风险
- 成交额太低
- 上市时间太短且 K 线不足
- 当日一字涨停不可参与的票，只保留观察，不作为可执行候选

---

## 6. 缠论三买结构定义

这里使用工程化三买，不追求完整理论复刻，先服务选股可跑通。

### 6.1 30 分钟三买

默认使用 30 分钟 K 线检测三买结构。原因是热点股往往已经走过日线三买阶段，日线条件会明显滞后；30 分钟级别更适合捕捉主升途中的回踩再启动。

要求：

1. 前面已形成明显上涨段。
2. 上涨后形成一个中枢 / 震荡平台。
3. 离开中枢向上突破。
4. 回踩不跌回中枢上沿或关键突破区。
5. 回踩结束后重新转强。

工程近似：

```text
最近 80~120 根 30 分钟 K 线内：
- 先有一段上涨：阶段涨幅 >= 25%
- 形成平台：20~40 根内高低波动收敛，重叠区明显
- 突破平台：收盘价站上平台上沿 3% 以上
- 回踩平台：最低价不有效跌破平台上沿 / 突破位
- 再启动：最近 1~5 根 30 分钟 K 重新放量阳线或收复 5/10 均线
```

### 6.2 日线背景与回退

日线不再作为默认三买触发级别，只保留两类用途：

- 判断个股是否仍在主升趋势背景中
- 当分钟 K 数据不可用或需要复盘旧口径时，通过 `--structure-period daily` 显式回退

当前 runner 默认参数：`--structure-period 30`，输出会带 `structure_period` / `structure_freq` 方便后续 AB 对比。

---

## 7. 主升趋势过滤

进攻型策略必须强调“主升”，不是低位修复。

硬条件建议：

1. 当前价 > MA20 > MA60
2. 当前价 > MA250，或 MA250 不可用时至少当前价 > MA120
3. 近 60 日涨幅为正，且强于沪深300 / 中证1000
4. 距离 120 日高点不太远：`close / rolling_120_high >= 0.75`
5. 近期不能连续放量长阴破位

---

## 8. 量价进攻确认

三买最好不是缩量躺平，而是回踩后重新被资金攻击。

加分项：

- 最近 10 日出现过涨停 / 大阳线
- 回踩期间缩量
- 再启动日放量：`volume / volume_ma5 >= 1.2`
- OBV 近 10 日抬升
- 收盘价接近日内高点

风险扣分：

- 突破后爆量长上影
- 回踩阴线量能接近或超过突破阳线
- 板块退潮但个股硬撑
- 严重异动临界且高位加速过猛

---

## 9. 排序评分

建议初版输出 `attack_score`：

```text
attack_score =
  theme_heat_score * 0.30
+ stock_leader_score * 0.20
+ third_buy_score * 0.25
+ trend_strength_score * 0.15
+ volume_attack_score * 0.10
- risk_penalty
```

### 9.1 stock_leader_score

- 板块内涨幅排名
- 是否板块涨停股 / 炸板修复 / 反包
- 是否连板高度股或中军容量股
- 成交额是否足够

### 9.2 third_buy_score

- 平台越清晰越高
- 突破越强越高
- 回踩越浅越高
- 回踩后再启动越明确越高

---

## 10. 初版落地文件建议

新增：

```text
app/core/providers/bigamap_provider.py
backtest/strategies/strategy_attack_third_buy.py
backtest/runners/run_select_attack_third_buy.py
docs/STRATEGY_ATTACK_THIRD_BUY.md
```

后续可选：

```text
backtest/runners/run_backtest_attack_third_buy.py
backtest/results_attack/
tests/test_bigamap_provider.py
tests/test_strategy_attack_third_buy.py
```

---

## 11. 实施顺序

### Phase 1：热点数据接入

- 拉取 BigAmap 涨停复盘
- 聚合行业/题材热度
- 拉取板块树图 / 板块排行
- 输出热点板块 TopN

验收：

```bash
uv run python -m pytest tests/test_bigamap_provider.py
```

### Phase 2：三买形态检测

- 实现日线平台 / 突破 / 回踩不破 / 再启动检测
- 输出 `third_buy_*` 元数据
- 先对单票调试，不急着全市场

### Phase 3：热点 + 三买融合

- 只在热点板块股票池里跑三买
- 输出 `attack_score`
- 保存 `current_attack_third_buy.json`

### Phase 4：回测与复盘

- 先做 50/100 样本回测
- 对失败票拆原因：热点退潮、三买失败、量价失败、追高风险
- 再调权重，不要先调硬条件

---

## 12. 风险提示

进攻型策略天然比 v3.1 更激进：

- 更容易追在题材末端
- 更依赖板块热度数据及时性
- 更需要市场环境过滤
- 更适合候选池，不适合自动交易

因此初版输出必须包含：

- 板块热度来源
- 三买结构证据
- 风险扣分原因
- 是否一字不可买 / 严重异动临界
- 信号生成时间
