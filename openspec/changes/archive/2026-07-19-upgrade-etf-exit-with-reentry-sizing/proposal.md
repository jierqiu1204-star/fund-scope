## Why

当前 ETF 退出规则在回测里只比“按综合排名 TopN 持有”略好，甚至可能因为过早止盈/止损而拖累收益。现在需要把退出规则从“卖出信号”升级为成熟系统常见的风险管理层：先判断是否暂停加仓、减仓、清仓，再定义重新入场条件，并用卖飞率、保护成功率和回撤改善来验证。

## What Changes

- 新增 ETF 退出后再入场与仓位调整能力：退出不再只有“卖出/不卖”，而是输出 `hold`、`no_add`、`reduce`、`exit`、`reentry_candidate` 等状态；可执行减仓/退出必须使用相对不可变 exposure baseline 的绝对剩余仓位目标，`take_profit_watch` 固定为 `hold/仅观察`，不得产生 trim/reduce/exit 动作。
- 将移动止盈、硬止损、趋势转弱拆成不同职责：
  - 硬止损：极端亏损保险。
  - 移动止盈：盈利保护，优先减仓而非默认清仓。
  - 趋势转弱：默认作为暂停加仓/降权 guard，只有和排名恶化、亏损、板块转弱同时出现时才升级。
- 新增再入场规则：只有 owner-confirmed partial/full execution fact 才能证明减仓/退出已发生并启动再入场冷却；建议生成、邮件发送/SMTP 接受、提醒 cooldown 或模拟成交均不得推进动作状态或冷却。执行后只有当综合排名、买点状态、板块趋势和冷却期同时满足时才重新允许加仓。
- 新增分桶参数与动态线：按 ETF 类型、行业/主题、波动率分位、持仓盈亏状态生成不同止损/止盈/回吐阈值。
- 新增退出规则证据指标：卖飞率、保护成功率、退出后再入场收益、减仓后机会成本、回撤改善、邮件/提醒次数。
- 回测必须同时比较：
  - TopN 固定持有。
  - 当前默认退出规则。
  - 退出规则 V2：仓位调整 + 再入场。
  - 仅 guard 不卖出策略。
- 候选规则仍然 research-only，不能自动修改实时邮件规则或真实持仓。

## Capabilities

### New Capabilities

- `etf-exit-reentry-sizing`: ETF 退出后的仓位调整、冷却、重新入场和分桶动态线规则。

### Modified Capabilities

- `tracked-position-exit-strategy`: 从单一退出提醒扩展为持仓处理状态、仓位动作和再入场状态。
- `etf-research-evidence-contract`: 证据契约需要记录退出规则版本、再入场版本、分桶参数来源和是否可影响实时规则。
- `etf-portfolio-backtest`: 回测需要支持 TopN 固定持有、当前退出规则、V2 仓位调整/再入场和 guard-only 策略对比。

## Impact

- 后端：`risk_alerts`、`tracked_positions`、`short_research` 证据/回测服务、ETF exit hyperopt、portfolio backtest、admin jobs。
- 前端：`/short-term` 持仓卡片、`/short-term/evidence` 策略证据页、ETF 工作台策略证据页面。
- 数据：优先使用现有 JSON 字段记录策略版本、分桶参数和证据摘要；只有需要长期审计时再新增兼容迁移。
- 运维：新增或扩展 nightly 研究任务，不影响盘中盯盘、真实邮件发送和用户手动交易边界。
- 边界：不连接券商，不自动交易，不让 AI 决定买卖；AI 只解释规则和证据。
- 生命周期协调：本 change 的风险管理研究目标保持不变，但动作、执行、通知和再入场冷却语义以后续 `harden-etf-alert-action-lifecycle` 契约为准；旧相对减仓和 recommendation-derived cooldown 仅可作为 legacy/research-only 证据。
