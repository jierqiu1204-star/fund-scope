## Context

上一变更 `harden-etf-end-to-end-risk-controls` 已完成报价 fail-closed、组合静态风险预算、V2 lifecycle shadow 和执行证据。本变更只补足其账户级和规模级缺口。实现必须遵守模块化单体依赖方向：行情事实由 market data 提供，持仓与成交事实由 tracked positions 保存，纯风险规则放在 risk alerts / portfolio allocation，跨域预取与持久化由 workflows 编排，notification 不参与风险判断。

## Goals / Non-Goals

**Goals:**

- 用可证明的 ETF sleeve NAV 取代“平均持仓盈亏=组合回撤”的错误口径。
- 在所有加仓/重新入场建议之前应用一个可持久化、可解释、可恢复的账户风险状态。
- 让流动性判断依赖实际计划金额，并对买入和退出采用不同的 fail-closed 语义。
- 以轻量、日频、仅最终组合的方式提供 clone/factor、边际风险和压力证据。
- 在列表、调度和影子生命周期中每个 owner/run 只构建一次风险上下文。

**Non-Goals:**

- 不修改综合排名分数、因子权重、入榜数量、发布门槛或龙头战法。
- 不接券商、不自动下单、不声称提醒等于成交。
- 不训练 ML/RL，不做参数网格搜索，不引入全量组合优化器或 Rust/C++ 重写。
- 不用缺失成交、默认资金、原始价、旧行情或后验回填制造历史账户净值。

## Decisions

### 1. ETF sleeve NAV is an evidence contract, not an inferred brokerage account

新增 append-only `TrackedEtfSleeveLedgerEvent` 与 `TrackedEtfSleeveSnapshot`。系统不能从默认资金、`buy_amount` 或估算份额倒推出真实账户：用户必须在某个 cutoff 显式对账“现金 + 全部活动 ETF 持仓数量/剩余成本”，该事件才建立真实 sleeve 起点。之后只接受有 provenance 的现金流、持仓变动、分红/费用/公司行动与 `owner_confirmed` 执行；任一未对账变动都会让后续状态进入 `data_halt`。快照保存 trade session、cutoff、现金、持仓市值、已实现/未实现盈亏、总权益、flow-adjusted NAV、高水位、估值覆盖率、执行覆盖率、状态、缺失原因和 source hash。

系统只称其为 `tracked_etf_sleeve`，不称为整个证券账户。任一活动持仓未被完整对账、缺少有效份额或合格估值、资金未确认、执行链不连续、外部现金流/分红/公司行动未知、权益非有限时，快照为 `unavailable`；不得以平均盈亏或最新排名价格替代。相同 source hash 幂等复用；输入被后续用户确认纠正时追加 correction/reconciliation 事件，不篡改旧证据。对账以前的历史净值永久保持 unavailable。

### 2. Risk state is append-only and asymmetric

风险状态作为每日 `TrackedEtfSleeveSnapshot` 的不可变字段保存，最新合格快照构成当前状态，避免在 owner 与每只 position 间复制一份可漂移状态：

- `normal`: 可继续产生正常研究型加仓/减仓建议；
- `reduce_only`: 禁止 `add` 和 `reentry_candidate`，允许持有、减仓、退出；
- `data_halt`: 估值/资金/执行证据不足，不产生新的增加风险建议，已有风险提示继续显示并明确数据限制。

触发优先级为 `data_halt > reduce_only > normal`。数据缺失、非有限、覆盖不完整立即进入 `data_halt`；真实 sleeve NAV 回撤超限或多个不同 eligible trade session/action cycle 的止损事件进入 `reduce_only`。恢复采用非对称滞回：降级立即，重新开放至少需要两个连续合格交易日、冷却期结束、回撤恢复到较宽松 release threshold，且不能依赖同一交易日重复运行。手工 override 必须形成审计事件，不修改历史快照。

信号止损、SMTP、用户确认执行分别计数；生产保护只使用去重后的 eligible signal cycle 和 owner-confirmed execution，不把提醒行数当成交次数。

### 3. Explicit capital confirmation is required for actionable sizing

`User.etf_trading_capital` 保持兼容读取，但新增 `etf_trading_capital_confirmed_at`。设置接口成功保存资金时写入时间。缺少确认时间时，只能返回目标权重和稳定 unavailable reason；不得使用 `DEFAULT_ETF_TRADING_CAPITAL` 输出金额或股数。计算边界拒绝 bool、NaN、无穷、零和负值。

### 4. Liquidity capacity separates entry gating from exit urgency

新增纯 `EtfLiquidityCapacityAssessment`：输入 side、计划金额或当前市值、20 日 decision-eligible 成交额中位数、新鲜 bid/ask、折溢价/IOPV、涨跌停和报价资格。输出正常/压力 participation、预计退出天数、spread、状态和原因。

版本 1 使用保守、可配置常量并保存 manifest/hash。买入/加仓缺失 ADV、报价不合格、价差过宽、结构异常或容量超限时变成 `no_add`；退出信号永远不因流动性差而被隐藏，只将执行证据标记为 `stressed` 或 `unavailable`，并显示可能分批和跳空风险。压力容量使用更低参与率和成交额减半，不以模拟成交替代真实订单。

### 5. Portfolio risk V2 remains lightweight and shadow-first

市场风险观测不再从 TopN 候选隐式推导。workflow 从同一 PIT run 单独取得合格宽基观测，要求一个版本化的固定类别集合和覆盖率；降风险可当日生效，重新升风险要求连续合格 session。

`PortfolioRiskBudget` 继续执行 v1 静态硬上限，并新增 v2 shadow metrics：

- clone group、主题、资产类别和 unknown 暴露；unknown 不再被视作已知分散；
- 最终最多 20 只、最近最多 120 个共同 PIT 复权收益；
- 对样本协方差做固定比例的对角收缩，输出组合波动和每只边际/总风险贡献；
- 固定压力场景：市场下跌、最大主题冲击、相关性趋一、波动翻倍、成交额减半且价差扩大、止损跳空；
- 输出最大压力损失、集中度、有效持仓数和 coverage/unavailable reason。

这些 v2 指标先进入 `risk_summary.policy_mode=shadow`，不连续缩放权重、不寻找最优参数。只有真实 PIT 样本、时间外验证和人工批准后才可另开变更晋升为硬门槛。

### 6. Owner risk context is batch-built and reused

新增 owner-scoped `EtfOwnerRiskContext`。列表 API、每日/盘中 workflow 先批量加载活动持仓、最新 owner-confirmed execution、去重 alert/action cycle、最新 NAV/risk event 和所需日线聚合；随后将同一 immutable context 传入各持仓 sizing。禁止在每个持仓内部重新执行 owner aggregate 查询。

盘中不重算 252 日协方差；使用最近 post-close portfolio risk snapshot。所有 owner/position 循环串行且有上限，单只失败隔离。数据库查询按 owner 和 position ids 批量，避免 N+1。

### 7. API additions remain compatible and provenance stays explicit

持仓响应新增可选 `risk_control` 与 `liquidity_capacity`；组合 `risk_summary` 新增 v2 shadow 字段；审计 context 保存同一 contract hash。旧客户端忽略新增字段即可。金额不可用时字段为 null，并返回稳定原因。

允许的 provenance 仍是 signal、notification、owner-confirmed、broker-confirmed 等离散事实；不存在的执行结果保持 unknown。`data_halt` 不得把缺失报告解释为空仓。

## Risks / Trade-offs

- [历史持仓缺少完整执行链] → 明确标记 execution coverage 不足并 `data_halt`；用户重新确认资金/份额后从该 cutoff 开始建立真实 sleeve，不伪造过去。
- [新门禁导致金额建议暂时消失] → 继续展示排名、目标权重和退出风险，提示用户确认资金；不恢复默认一万元。
- [流动性阈值过严] → v1 阈值版本化并先记录容量分布；退出永不被阻断，新增风险才 fail closed。
- [风险状态抖动] → 降级即时、升级需要连续 session 和 release threshold；同日重跑幂等。
- [组合矩阵占用资源] → 仅 post-close、最多 20×252，纯 Python 有界计算；绝不对权威池全矩阵运行。
- [风险 context 在 API 与 scheduler 口径分叉] → 共用一个 workflow/service builder 和 contract hash，测试不同调用入口结果一致。

## Migration Plan

1. 新增 nullable 资金确认时间、append-only sleeve ledger 和 daily snapshot 表；迁移不猜测现有 1 万元、历史现金或持仓是否由用户确认，不做数据回填。
2. 部署纯规则、模型和读取 API；新 portfolio v2 指标保持 shadow。
3. 启用显式资金与 owner risk state 的 `no_add` 门禁；退出提醒保持原行为并附容量证据。
4. 每日收盘后建立 sleeve NAV/risk event；盘中复用最近合格状态并只做报价/容量增量检查。
5. 观察真实 session、覆盖和状态转换；后续只有独立 OpenSpec 与人工批准才能把 v2 shadow 变成硬权重约束。
6. 回滚时关闭 risk-state enforcement 和 v2 shadow flag；保留证据表，继续执行旧退出提醒和 v1 静态组合上限。
