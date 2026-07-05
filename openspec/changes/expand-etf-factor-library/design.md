## Context

FundScope 的 ETF 短线研究链路已经包含技术评分、板块趋势、主题催化、事件热度、信号 run 缓存和详情页一致性约束。项目规则要求金融场景宁可显示“暂无/等待数据”，不能用旧行情、估算值或 fallback 数据输出误导性结论。

用户希望把价格动量、反转过热、波动风险、流动性、板块趋势、主题事件、资金流、情绪热度、基本面质量、估值、宏观风格和 ETF 结构全部纳入项目。这个需求的核心不是一次性把所有因子都加权相加，而是建立一个可扩展、可审计、能表达缺失状态的 ETF 多因子层。

## Goals / Non-Goals

**Goals:**

- 建立 ETF 因子库，统一定义因子分类、来源、计算窗口、方向、可靠性、可用状态和评分用途。
- 将综合关注分升级为版本化 factor profile，支持完整口径和缺失数据降级口径。
- 覆盖用户列出的完整因子层，并明确哪些因子做正向评分、哪些做风险扣分、哪些做硬门槛、哪些仅做展示解释。
- 继续保证榜单、详情页和最新成功 signal run 使用同一份缓存因子结果。
- 让机器人、创新药、半导体、光模块等主题 ETF 可以通过同一套因子框架比较。
- 缺少真实来源时输出 `null`、`unavailable` 或 `display_only`，不输出中性 fallback 数字。

**Non-Goals:**

- 不在本次变更中接入所有外部数据供应商。
- 不引入外部 AI 直接修改最终分数。
- 不新增自动交易、买卖建议、目标价、收益预测或保证收益表述。
- 不改变组合配置、持仓追踪、风控提醒或通知发送的领域职责。
- 不让基本面、情绪或宏观因子绕过数据可靠性校验。

## Decisions

### 1. 因子层属于 Research Signal，不属于 Market Data

新增因子模块应放在 `app.services.short_research` 边界下，例如：

```text
app.services.short_research
  metrics / technical scoring
  theme taxonomy
  sector trends
  factors
  opportunity scoring
```

行情层继续只提供原始行情、ETF 元数据、日线、净值和可靠性标签。因子层读取行情层和研究层已有输出，生成研究信号。这样符合现有后端领域边界：Market Data -> Research Signal -> Portfolio Allocation。

备选方案是新增顶层 `app.services.etf_factors`。它更适合未来拆分，但当前阶段会增加跨模块依赖和测试成本，因此 v1 先放在短线研究边界内。

### 2. 每个因子输出统一 FactorResult

每个因子输出统一结构，而不是只返回一个数字：

```text
factor_id
group
label
score
direction
usage
availability
reliability
source
as_of_date
window
raw_value
reason
components
```

关键枚举：

- `usage`: `score`, `penalty`, `gate`, `display`
- `availability`: `available`, `insufficient_data`, `stale`, `unavailable`, `display_only`
- `reliability`: `verified`, `alternate_provider`, `seed_only`, `estimated`, `stale`, `unavailable`
- `direction`: `higher_is_better`, `lower_is_better`, `band_is_better`, `risk_only`

这样可以表达“资金流真实可用所以参与评分”和“情绪热度只有 seed-only 所以只能展示”的区别。

### 3. 因子分组完整覆盖，但分阶段落地

因子库一次性定义完整分类：

| Group | Examples | Usage |
| --- | --- | --- |
| price_momentum | 5/20/60 日收益、相对强弱、趋势延续 | score |
| reversal_overheat | 偏离均线、短期涨幅过快、冲高回落、长短期反转 | penalty/gate |
| volatility_risk | 20 日波动、最大回撤、下行波动、跳空风险 | penalty/gate |
| liquidity | 成交额、换手、成交额放大、买卖价差 | gate/score |
| sector_trend | 同主题均值收益、站上均线比例、上涨家数 | score |
| theme_event | IPO、政策、订单、产业大会、产品发布、药审节点 | score/display |
| fund_flow | ETF 份额变化、净申购、主力资金、北向/行业资金 | score/display |
| sentiment_heat | 新闻数量、社媒热度、搜索热度、研报覆盖 | score/display |
| fundamentals_quality | ROE、盈利稳定性、现金流、利润增速的成分股聚合 | display/score |
| valuation | PE/PB、股息率、ERP、行业估值分位 | score/penalty |
| macro_style | 利率、信用、汇率、商品、风险偏好、大小盘/成长价值 | display/score |
| etf_structure | 折溢价、跟踪误差、规模、费率、基金公司、成分集中度 | gate/penalty/display |

v1 实现优先级：

1. 从已有数据可直接计算的技术、过热、波动、流动性、板块趋势、ETF 结构基础项。
2. 需要较少新数据的资金流和成分股扩散。
3. 需要可靠外部源的估值、宏观、真实情绪和基本面聚合。

### 4. 综合关注分使用 versioned factor profile

综合关注分不再只有一个固定权重公式，而是由 `factor_profile_version` 决定。

推荐 v1 完整口径：

```text
technical_momentum      30%
sector_trend            15%
theme_event             10%
fund_flow               15%
constituent_breadth     10%
valuation_macro          5%
liquidity_structure     10%
risk_adjustment          5%
```

其中风险、流动性和 ETF 结构既可以参与打分，也可以作为硬门槛。实际代码可以先按可用因子降级：

```text
full_profile
technical_sector_theme_profile
technical_sector_flow_profile
technical_sector_only_profile
technical_only_unavailable_profile
```

只有技术分可用时，`opportunity_score` 应为 `null` 或显示“暂无综合关注”，不能把技术分包装成完整综合分。

### 5. 风险硬约束独立于加分项

以下风险不能被主题热度、板块趋势或资金流直接抵消：

- 冲高别追、短期过热、偏离均线过大
- 流动性不足或成交额异常低
- 数据不足、数据滞后、来源不可靠
- ETF 规模过小、异常折溢价、跟踪误差异常
- 最大回撤或波动过高

输出上保持两层：

```text
opportunity_score: 综合关注分
risk_gates: 是否限制观察等级、排序展示或组合权重
```

### 6. 缺失数据不参与评分

任何因子缺失时：

- API 对该因子输出 `score = null`
- 输出 `availability` 和 `reason`
- 不用 `50`、`60`、`0` 或旧缓存补位
- 权重 profile 明确说明该因子未参与本次综合评分

如果外部数据只有 seed、静态词表或示例快照，则最多标为 `display_only`，不能标为真实情绪、真实资金流或真实事件热度。

### 7. 信号 run 是评分唯一可信输出

因子结果和综合关注分写入 signal item JSON：

- `factor_profile_version`
- `factor_scores`
- `factor_group_scores`
- `factor_availability`
- `risk_gates`
- `opportunity_score`
- `opportunity_score_version`
- `opportunity_breakdown`

详情页继续优先读取最新成功 ETF signal run 中的缓存 item，不现场对单只 ETF 重新归一化。

## Risks / Trade-offs

- [Risk] 因子过多导致用户误以为总分更精确。  
  Mitigation: 前端展示 factor profile 和缺失因子，保留“观察/研究”语言，不输出收益预测。

- [Risk] 部分因子高度相关，重复加分。  
  Mitigation: 先按 group 聚合，再进入 profile；同组内只给有限权重。

- [Risk] 外部数据质量参差，资金流或情绪源可能误导。  
  Mitigation: 未验证来源默认 `display_only` 或 `unavailable`，不参与评分。

- [Risk] 一次实现全部因子会拖慢交付。  
  Mitigation: 规格完整定义，任务分阶段实现；v1 优先已有数据和高价值 ETF 因子。

- [Risk] 小样本主题的成分股扩散或板块趋势失真。  
  Mitigation: 输出样本数、覆盖率和 `insufficient_data`，样本不足不打分。

## Migration Plan

1. 新增因子库类型、注册表、FactorResult 输出和测试夹具。
2. 迁移已有技术、过热、波动、流动性、板块趋势、主题催化到统一因子输出。
3. 增加 ETF 结构基础因子：规模、成交额门槛、跟踪误差/折溢价可用状态。
4. 增加资金流和成分股扩散因子；没有真实源时输出 unavailable/display-only。
5. 建立 factor profile 合成器和降级权重版本。
6. 扩展 API schema 和前端展示。
7. 补齐后端、API、前端测试，并运行领域边界检查。
8. 部署后重新生成 ETF signal run，手动检查机器人、创新药、半导体、光模块和无主题 ETF。

Rollback：保留旧技术分、板块趋势和主题催化字段。若 factor profile 失败，API 返回旧字段和 `factor_profile_status = unavailable`，不输出新综合关注 fallback。

## Open Questions

- 资金流优先使用 ETF 份额变化、交易所申赎、主力资金，还是第三方资金流数据。
- 成分股扩散是否需要先落地成分股持仓同步任务。
- 估值和基本面聚合用指数层数据还是 ETF 成分股加权聚合。
- 宏观/风格因子第一版是否只做展示解释，还是进入小权重评分。
- ETF 折溢价和 IOPV 数据源是否已经足够稳定，能否进入硬门槛。
