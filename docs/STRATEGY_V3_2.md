# A 股选股策略文档 v3.2

> 状态：排序版候选主策略  
> 项目：`a-stock-analyst`  
> 策略代号：`v3.2`  
> 设计来源：基于 v3.1 回测验证结果，把最强信号提升为主排序依据。

---

## 1. 策略定位

`v3.2` 不是推翻 `v3.1`，而是在 `v3.1` 单一真源判定逻辑上，增加**信号分级**与**排序评分**。

一句话概括：

> **先沿用 v3.1 的周线定方向 + 日线抓回调，再根据历史验证结果，把不同信号按强弱分层排序。**

它主要解决的问题不是“能不能找到买点”，而是：

1. 同时命中多只股票时，**谁应该排前面**
2. 三类信号不等权时，**如何体现强弱差异**
3. 如何把“命中结果”升级成“可排序候选池”

---

## 2. 为什么需要 v3.2

在 fallback 股票池的回测验证中，三类信号表现已经拉开差距：

- `zero_axis_pullback`：最强，平均收益 / 中位数 / 胜率都领先
- `higher_low`：第二梯队，偏稳健
- `macd_area_divergence`：第二梯队，覆盖广但噪音更大

所以 v3.2 的核心判断是：

> **三类信号不能再等权处理。**

---

## 3. 继承自 v3.1 的部分

v3.2 完全继承以下规则，不另起炉灶：

- 周线最后一笔必须是向上笔
- 日线必须存在有效向下笔回调
- 回调幅度至少 `3%`
- 买点必须发生在最近 `7` 天内
- 信号来源仍是三类：
  - `zero_axis_pullback`
  - `higher_low`
  - `macd_area_divergence`

也就是说：

> **v3.2 不改变“是否命中”，只改变“命中后如何排序”。**

---

## 4. v3.2 新增：信号优先级

### 4.1 优先级定义

- `P1` → `zero_axis_pullback`
- `P2` → `higher_low`
- `P3` → `macd_area_divergence`

对应基础分：

```python
SIGNAL_BASE_SCORES = {
    'zero_axis_pullback': 100.0,
    'higher_low': 75.0,
    'macd_area_divergence': 70.0,
}
```

设计原则：

- `zero_axis_pullback` 直接给最高权重
- `higher_low` 略高于 `macd_area_divergence`
- 保留二者的补充价值，但不再和 P1 同权

---

## 5. v3.2 新增：排序分数 `signal_score`

最终排序分数由以下部分组成：

### 5.1 Base Score（信号基础分）

由信号类别直接决定：

- `zero_axis_pullback` → `100`
- `higher_low` → `75`
- `macd_area_divergence` → `70`

### 5.2 Pullback Bonus（回调幅度加分）

```python
pullback_bonus = min(pullback_pct * 1.5, 18.0)
```

含义：

- 回调越充分，加分越高
- 但最多封顶 `18`
- 防止极端深回调把排序完全扭曲

### 5.3 Freshness Bonus（新鲜度加分）

```python
freshness_bonus = max(0.0, 7.0 - days_ago) * 1.5
```

含义：

- 越新的信号，分数越高
- 最近 1~2 天命中的信号更优先
- 过了 7 天本来就不会入选

### 5.4 Momentum Bonus（动能质量加分）

- `zero_axis_pullback`：固定加 `10`
- 其他信号：根据 `area_last / area_prev` 衰减比例动态加分

目的：

- 突出“回调中的弱转强”
- 让标准背驰里动能衰减更明显的票排更前

### 5.5 Structure Bonus（结构强度加分）

```python
structure_bonus = 6.0 if higher_low else 0.0
```

含义：

- 即使主信号不是 `higher_low`，若同时具备低点抬高结构，也给额外加分
- 体现“强支撑回调”的附加质量

---

## 6. 排序规则

候选结果最终按以下顺序排序：

```python
results.sort(key=lambda x: (-x['signal_score'], x['days_ago'], -x['pullback_pct']))
```

也就是：

1. **先看总分高低**
2. **分数相同时，优先更近的买点**
3. **再看回调幅度是否更充分**

---

## 7. 输出增强

相比 v3.1，v3.2 输出新增字段：

- `signal_priority`
- `signal_score`
- `score_breakdown`
- `avg_signal_score`
- `max_signal_score`
- `signal_priority_breakdown`
- `top_signal`
- `top_ranked_signals`

这让结果不仅能看“是否命中”，还能看：

- 哪只票排第一
- 为什么排第一
- 分数由哪些部分构成

---

## 8. 当前适用场景

v3.2 更适合：

- 每日候选池排序
- Web 页面 Top N 推荐
- 盘后复盘时优先看最强票
- 回测时比较不同分层信号的收益质量

而 v3.1 仍适合：

- 作为纯命中基线
- 做信号发现，不做强排序

---

## 9. 当前结论

当前验证结论可以直接固化为一句话：

> **v3.2 = 用 v3.1 找买点，用排序分数决定先看谁。**

其中核心主张是：

> **`zero_axis_pullback` 应该被视为主信号，而不是与其他两类等权。**

---

## 10. 下一步优化方向

v3.2 已经能用，但还可以继续进化：

1. 把实时 runner 也升级成 v3.2 输出
2. 给分数加入成交额/换手率/波动率因子
3. 扩大 fallback 股票池，验证分层是否在更大样本上仍成立
4. 增加 CSV / Top N 报表导出

---

## 11. 文件位置

- 排序策略：`backtest/strategies/strategy_v3_2_ranked.py`
- 排序回测入口：`backtest/runners/run_v3_2_backtest.py`
- v3.1 基线文档：`docs/STRATEGY_V3_1.md`

---

## 12. 总结

v3.2 的本质不是“重写策略”，而是把回测验证结果工程化：

- 保留 v3.1 作为判定基线
- 承认三类信号强弱不同
- 用优先级和分数把候选池排序

这一步做完后，策略开始从“命中型工具”升级为“决策型工具”。
