## Context

FundScope 当前 ETF 工作台已经有短线排序、横截面排名、动态买点、标签验证、组合参考、持仓追踪和 AI/规则解释。现有边界明确：行情数据只提供可信输入，短线研究产出评分和标签，组合配置读取研究结果，持仓和风控不反向影响研究层；AI 只能解释确定性结果，不能改分、改标签、改权重或触发邮件。

主题催化层要解决的是另一个维度：机器人、半导体、光模块等 ETF 的市场关注度会受到 IPO、政策、订单、产品发布和产业新闻影响。这个信息不属于行情层，也不应直接进入组合或邮件层；它属于 Research Signal 层的结构化研究证据。

## Goals / Non-Goals

**Goals:**

- 在研究信号层新增 ETF 主题催化能力，输出可审计的 `catalyst_score`、`sentiment_heat_score` 和 `opportunity_score`。
- 保留现有 `total_score` 作为短线技术/买点/风险分，不让催化覆盖买点和风控。
- 第一版权重固定为 `technical_score 70% + theme_catalyst_score 20% + news_sentiment_score 10%`，后续通过回测校准。
- 支持人工维护的结构化催化事件作为 v1 数据源，并为后续 LLM 候选事件抽取留接口。
- 在 `/short-term` 明确展示“主题强 / 买点风险 / 数据限制”的组合判断。

**Non-Goals:**

- 不把 AI 输出直接作为分数、标签、组合权重或邮件触发输入。
- 不自动下单、不连接券商、不修改真实持仓。
- 不改变现有风险告警、邮件提醒、组合优化默认规则。
- 不做全网实时新闻爬虫或研报付费数据接入。
- 不把主题催化写成不可审计的 prompt 文本或前端常量。

## Decisions

### 1. 催化层放在 Research Signal，不放在 Market Data 或 Portfolio Allocation

新增模块放在 `app.services.short_research` 的子模块，例如 `theme_catalysts.py`，或等价的研究信号子模块。它读取主题分类、人工/候选催化事件、新闻热度快照和短线 signal item，产出研究评分字段。

理由：主题催化是研究判断，不是原始行情，也不是仓位模型。组合层可以读取 `opportunity_score` 做展示或对照，但 v1 不用它自动改变权重。

备选方案是放入 `market_data`，但会让行情层承担主观研究职责；放入 `portfolio_allocation` 又会跳过短线研究证据链，均不采用。

### 2. 使用结构化事件和快照，AI 只产生候选

新增两类持久化数据：

- 主题催化事件：主题、事件类型、标题、摘要、来源 URL、事件日期、有效期、方向、强度、可信度、状态、来源类型。
- 主题催化快照：日期、主题、催化分、新闻/情绪热度分、事件数量、核心事件摘要、数据限制、生成时间。

v1 允许人工维护事件并由后台任务生成快照。后续 LLM 可以把新闻文本抽取成 `pending` 候选事件，但必须经规则校验或人工确认后才能进入 active 评分。

### 3. 保留 `total_score`，新增 `opportunity_score`

现有 `total_score` 继续表示短线技术结构、动态阈值、标签证据、数据可信度、流动性/折溢价。新增：

- `technical_score`: 默认等于当前 `total_score`。
- `catalyst_score`: 主题催化强度分，来自 active 事件和有效期衰减。
- `sentiment_heat_score`: 新闻/关注热度分，v1 可由事件数量和最近性近似。
- `opportunity_score`: `technical_score * 0.70 + catalyst_score * 0.20 + sentiment_heat_score * 0.10`。
- `opportunity_label`: 例如 `重点观察`、`主题强但等买点`、`技术优先`、`等待数据`。

`opportunity_score` 用于展示和可选排序；`total_score`、`conclusion`、`entry_timing_label` 仍决定短线买点解释。

### 4. 风险和数据可信度是硬约束

当 signal item 存在 `数据不足`、`数据滞后`、`流动性不足`、`冲高别追`、`跌破等待`、`放量转弱` 等状态时，主题催化不得清除这些风险标签。

限制规则：

- 数据不可决策时，`opportunity_label` 必须显示等待数据或仅展示主题热度。
- `entry_timing_label = 冲高别追` 时，强催化只能输出 `主题强但等买点`，不能输出适合追入。
- 低流动性或折溢价异常时，综合关注分可以展示，但组合层不得因此给权重。

### 5. API 通过兼容扩展暴露

不新增必需 URL。扩展短线研究响应和 `score_breakdown`/`metrics` JSON：

- `opportunity_score`
- `opportunity_label`
- `catalyst_score`
- `sentiment_heat_score`
- `catalyst_summary`
- `catalyst_events`
- `catalyst_limitations`
- `opportunity_breakdown`

没有催化数据时返回空摘要和中性分，不影响旧客户端。

### 6. 第一版先种子机器人/半导体/光模块相关主题

v1 人工种子覆盖：

- 机器人/具身智能：宇树科技 IPO、产业融资、产品发布、政策扶持等。
- 半导体/芯片：政策、国产替代、先进封装、设备材料。
- 光模块/CPO/光通信：AI 算力链、800G/1.6T 光模块、CPO、数据中心资本开支。

ETF 通过现有 `theme_taxonomy` 归到主题；光模块没有精确 ETF 时，允许展示“无精确 ETF，匹配通信/5G/信息技术代理主题”的限制。

## Risks / Trade-offs

- [Risk] 催化分看起来像预测涨跌。  
  Mitigation: UI 文案统一为“综合关注/主题催化”，禁止“必涨、推荐买入、目标收益”等表达。

- [Risk] 人工催化事件过期后继续影响排序。  
  Mitigation: 每条事件必须有有效期，快照任务对过期事件降权或排除，并显示数据限制。

- [Risk] LLM 候选事件幻觉或夸大影响。  
  Mitigation: LLM 输出只进 `pending`，active 评分只使用有来源、有日期、有可信度的结构化事件。

- [Risk] 主题催化把短线过热 ETF 推得太高。  
  Mitigation: `opportunity_score` 和 `total_score` 分开展示，`冲高别追` 等买点标签永远保留。

- [Risk] 新增字段让页面复杂。  
  Mitigation: 列表卡只显示综合关注分、技术分和一句催化摘要；完整事件放详情页展开区。

## Migration Plan

1. 新增主题催化事件和快照模型、迁移和只读服务。
2. 新增人工种子数据和后台刷新任务，先生成机器人、半导体、光模块相关主题快照。
3. 在 ETF 信号生成后合成 `opportunity_score` 和 catalyst breakdown，写入现有 signal item JSON。
4. 扩展 API schema，保持旧字段兼容。
5. 更新 `/short-term` 列表、详情和筛选排序展示。
6. 增加后端和前端测试，验证强催化不覆盖风险标签。
7. 部署后手动运行主题催化刷新和 ETF 短线信号任务，确认页面显示新字段。

Rollback：保留旧 `total_score` 和旧 UI 逻辑；如果催化任务失败，API 返回空催化摘要和中性/不可用状态，页面仍按现有短线排序工作。

## Open Questions

- v2 是否要将 `opportunity_score` 接入组合配置对照，需要等回测和人工观察后再定。
- 新闻热度是否使用现有基金新闻表扩展，还是新增 ETF/主题新闻源，第一版不强制。
- 光模块主题没有精确 ETF 时，代理 ETF 的主题匹配规则需要根据实际持仓成分或指数说明再细化。
