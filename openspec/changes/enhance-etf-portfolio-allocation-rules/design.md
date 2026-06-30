## Context

FundScope 当前 `/short-term` 的 ETF 资金配置参考已经有单只上限、数据可靠性、主题集中度和防守/等待概念，但实际效果偏硬：高分 ETF 若被标为 `高位观察` 往往只能进入观察组，候选不足时组合会进入 `cash_wait`。这和成熟框架的分层思想不一致。

成熟项目的可借鉴点：
- QuantConnect 把信号、组合目标和风控拆开，信号不直接等于买入权重。
- PyPortfolioOpt 使用权重上下限、波动、协方差和约束做组合优化。
- Backtrader 用 `Sizer` 把买卖信号和仓位大小分离。
- Freqtrade / vectorbt 强调止损、移动止盈、保护期和不强行交易。

本次采用模块化单体内的确定性组合规则，不引入新依赖，不改变 API URL、数据库核心枚举或真实交易边界。

## Goals / Non-Goals

**Goals:**
- 将 ETF 组合从“硬筛选后等权/空仓”改成“分层候选 + 动态权重 + 风险约束”。
- 让 `高位观察 + 健康回踩/趋势延续` 可以进入小权重观察层，而不是一律零权重。
- 市场不好时支持防守 ETF 或现金等待，但必须有清楚原因。
- 每个权重和排除结果都能解释：标签、买点、波动、流动性、相关性、主题集中度、数据可靠性。
- `/short-term` 页面能直观看到进攻、防守、观察和等待资金的区别。

**Non-Goals:**
- 不自动下单，不连接券商，不输出“必须买入”。
- 不新增 PyPortfolioOpt、cvxpy 等重依赖。
- 不把场外基金纳入本次 ETF 组合权重改造。
- 不改变追踪持仓邮件提醒阈值。
- 不重做全站 UI。

## Decisions

### 1. 使用分层候选，而不是单一硬门槛

规则分为四层：
- `primary`: 主配置候选。要求 `短线观察`，买点为 `健康回踩` 或 `趋势延续`，数据可靠、流动性合格。
- `satellite`: 小仓观察候选。允许 `高位观察` 且买点仍健康，但单只和总仓位更低。
- `defensive`: 防守候选。国债、货币、黄金、红利、低波动宽基等，允许买点不强但不能明显转弱或数据不足。
- `watch_only`: 仅观察。冲高别追、跌破等待、放量转弱、数据不足、行情滞后、严重折溢价或低流动性。

替代方案：继续硬门槛。放弃原因：会导致强势但高位的资产全部零权重，页面经常空组合，解释力差。

### 2. 第一版使用确定性风险权重，不引入优化求解器

初始权重建议：

```text
base_score = normalized_score
entry_multiplier = 按买点标签调整
label_multiplier = 高位观察降权
liquidity_multiplier = 成交额和数据可靠性调整
volatility_penalty = 1 / max(volatility_unit, min_volatility)
raw_weight = base_score * entry_multiplier * label_multiplier * liquidity_multiplier * volatility_penalty
```

再统一应用约束：
- 单只 ETF 最高 `30%`。
- `satellite` 总权重上限建议 `30%-40%`。
- 单一行业/主题总权重上限建议 `40%`。
- 高相关 ETF 去重或降权。
- 权重低于最小展示阈值的 ETF 转入观察组。

替代方案：直接接入 PyPortfolioOpt。放弃原因：当前主要瓶颈是规则解释和候选分层，不是数学求解；先用可解释规则更稳。

### 3. 市场状态决定进攻、防守、现金上限

组合模式：
- `risk_on`: 主配置候选足够，允许高 ETF 暴露。
- `neutral`: 主配置不足但仍有健康候选，使用主配置 + 小仓观察 + 防守混合。
- `defensive`: 进攻候选不足，防守候选足够。
- `cash_wait`: 进攻、防守、小仓观察都不足或数据不可信。

现金不是失败状态；它代表当前规则不愿意强行给买入权重。

### 4. 保持旧接口兼容，扩展解释字段

保留现有字段：
- `items`
- `watch_only_items`
- `excluded_items`
- `cash_weight`
- `weight_sum`

扩展字段：
- `portfolio_mode`
- `market_regime`
- `primary_weight`
- `satellite_weight`
- `defensive_weight`
- `cash_reason`
- `allocation_layers`
- `constraint_summary`
- `weight_reason`
- `exclusion_reason`

旧前端没有新字段时仍按现有展示。

### 5. 页面展示不说“推荐买入”

前端使用：
- `ETF 资金配置参考`
- `主配置`
- `小仓观察`
- `防守配置`
- `等待资金`

避免：
- `建议买入`
- `必买`
- `稳赚`
- `目标收益`

## Risks / Trade-offs

- [Risk] 分层规则可能让组合从空仓变为有小仓权重，用户误解为强买信号。  
  → Mitigation：UI 明确区分“主配置”和“小仓观察”，并展示不是交易指令。

- [Risk] 不引入优化库会让权重不如 PyPortfolioOpt 数学严谨。  
  → Mitigation：第一版保持可解释和可测试；后续可在同一 contract 下替换权重求解器。

- [Risk] 防守 ETF 也可能下跌。  
  → Mitigation：防守只代表波动/相关性较低，不显示为低风险保证。

- [Risk] 行业标签或相关性数据不足会影响集中度约束。  
  → Mitigation：缺失时保守降权或转观察，不用缺失数据硬凑权重。

- [Risk] 当前实时行情和日线快照时间不一致可能影响组合。  
  → Mitigation：组合生成记录 `quote_time`、`daily_signal_date`、`portfolio_generated_at`，并在页面展示。
