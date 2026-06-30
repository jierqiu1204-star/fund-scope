## Context

当前 FundScope 的 ETF 研究链路已经包含行情、短线信号、ETF 资金配置、标签验证、回测、追踪和提醒。但这些模块曾经出现过口径不一致的问题：页面展示的是一个策略，模拟盘或回测可能跑的是另一套策略；实时行情和日线缓存也可能混用。用户现在更关心“这些标签和组合历史上表现如何”，因此必须先定义统一证据契约。

本设计不新增一套策略，也不替代以下未实施变更：
- `add-short-term-label-filter`: 负责标签筛选 UI。
- `add-etf-strategy-comparison-backtests`: 负责策略对照回测。
- `enhance-etf-portfolio-allocation-rules`: 负责 ETF 组合分层和权重规则。

本设计只定义这些能力之间的共享 contract、版本追踪和层级边界。

## Goals / Non-Goals

**Goals:**
- 建立一条 ETF 研究证据链：行情 → 信号 → 组合 → 回测/标签验证 → 页面分析。
- 让页面展示的标签、组合和历史证据能证明是否同源。
- 让回测和标签验证只能消费 contract，而不是复制页面或组合代码。
- 用测试/脚本约束后端模块依赖方向，防止跨层互相调用。
- 旧口径结果必须标记为旧证据，不能当作当前策略证据。

**Non-Goals:**
- 不实现新的 ETF 策略。
- 不实现标签筛选 UI。
- 不实现具体组合权重公式。
- 不改变邮件提醒触发条件。
- 不连接券商，不自动交易。
- 不引入事件总线或拆微服务。

## Decisions

### 1. Contract 是跨模块唯一共享语言

定义四类轻量 contract：

```text
ResearchSignalContract
  asset_type, asset_code, signal_run_id, signal_date, score, observation_label,
  entry_timing_label, data_reliability, source_data_time, rule_version

AllocationContract
  portfolio_run_id, allocation_version, portfolio_mode, market_regime,
  target_weights, allocation_layers, constraints, data_as_of_time

ReplayContract
  replay_run_id, signal_rule_version, allocation_version, execution_model,
  fee_model, date_range, data_cutoff, contract_hash

EvidenceSummary
  contract_hash, evidence_status, label_outcome_stats, backtest_metrics,
  sample_count, coverage, caveats
```

Contract 可以先用 Python dataclass / Pydantic schema 表达，持久化时优先进入现有 JSON 字段。

### 2. 明确层级依赖方向

允许方向：

```text
Market Data
  ↓
Research Signal
  ↓
Portfolio Allocation
  ↓
Backtest / Validation
  ↓
Short-Term Presentation

Position Tracking
  ↓
Risk Alert
  ↓
Notification
```

编排层例外：
- Scheduler / Admin / API / Workflow 可以调用多个领域服务，但不能存放核心策略算法。

禁止方向：
- Market Data 不得 import Research / Portfolio / Tracking / Notification。
- Research Signal 不得 import Tracking / Risk Alert / Notification。
- Portfolio Allocation 不得 import Tracking / Risk Alert / Notification。
- Backtest / Validation 不得 import frontend、API route 或复制组合规则。
- Notification 不得 import Market Data / Research / Portfolio。

### 3. 回测和验证只消费快照，不反向影响线上策略

回测和标签验证输入必须是：
- 历史行情。
- 历史信号 contract。
- 历史组合 contract。
- 明确执行模型和费用模型。

它们的输出只能作为 evidence summary 展示，不能自动改写线上标签、组合、追踪或邮件阈值。

### 4. 证据状态必须可见

页面展示状态：
- `同源已验证`: 当前信号/组合版本有匹配回测或标签验证。
- `等待验证`: 当前版本还没有完成证据。
- `样本不足`: 样本数或未来窗口不足。
- `版本不一致`: 页面策略版本和证据版本不同。
- `旧口径结果`: 旧模拟盘/旧策略实验结果，不能证明当前 ETF 工作台。

### 5. 先做边界和元数据，后做更复杂的实验平台

不做自由因子表达式引擎。原因：那是股票多因子研究平台的复杂能力；FundScope 当前主线是 ETF 短线研究和持仓提醒。先保证当前 ETF 标签、组合、回测、验证同源，比引入表达式 DSL 更重要。

## Risks / Trade-offs

- [Risk] Contract 字段设计过重，实施成本上升。  
  → Mitigation：第一版只记录必要版本、数据时间、标签、权重、执行模型和摘要，不要求完整事件溯源。

- [Risk] 旧回测结果会被用户误认为当前策略证据。  
  → Mitigation：所有旧结果没有 contract hash 时统一显示“旧口径结果”。

- [Risk] 依赖检测误报导致开发不便。  
  → Mitigation：只检测明确禁止的跨层 import，不限制编排层。

- [Risk] 未实施的组合和回测变更会改变 contract 内容。  
  → Mitigation：本变更定义 contract 扩展点，不固化具体组合公式和具体策略列表。

- [Risk] 多个 active changes 都触及 `/short-term`。  
  → Mitigation：本变更只增加 evidence status 区域和字段，不改筛选交互、不改组合布局主逻辑。
