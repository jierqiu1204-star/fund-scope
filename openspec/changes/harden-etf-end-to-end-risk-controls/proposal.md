## Why

FundScope 已经具备严格的数据准入、ETF 排名风险因子和持仓退出规则，但生产调度仍只调用旧持仓提醒路径，新版幂等生命周期没有持续收集影子证据；同时观察组合仍可能被重新归一化到接近满仓，缺少组合级风险预算，提醒与回测对跳空、价差和滑点的可执行性说明也不完整。需要在不改变综合排名、不自动交易、不扩大服务器负担的前提下，把这些已有能力接成一条可验证、可回滚的端到端风控链。

## What Changes

- 在现有盘中和每日持仓检查编排中加入 V2 lifecycle 影子评估；继续由旧链路产生当前页面与邮件结果，影子路径只写隔离证据，不生成动作、不发送通知，单只失败不得阻断其他持仓或行情任务。
- 修正 ETF 动作邮件的最终数据门槛：无论盘中还是每日任务，都必须使用新鲜、显式 `decision_eligible=true` 的盘口证据；旧盘中价、日线收盘或缺失资格字段只能网页展示，基金确认净值逻辑保持不变。
- 为 ETF 观察组合增加确定性的组合风险预算：依据 `risk_on/neutral/defensive/cash_wait` 设置风险资产暴露上限和最低现金权重，叠加单只、主题、相关簇、波动、回撤和流动性约束；缩减后的风险权重不得再次归一化回满仓，证据不足时 fail closed。
- 在持仓评估和研究回测中补充可执行性风险证据，包括参考价、可执行价来源、隔夜/信号到成交跳空、价差、基础滑点、压力滑点、费用和无法证明成交时的 unavailable reason；这些证据只影响可信度、观察权重或模拟成本，不把提醒解释为成交。
- 为上述规则增加有界、幂等、无生产副作用的测试，并保留关闭 V2 影子评估和退回既有组合风险契约的明确回滚路径。
- 不改变 ETF 综合排名因子或权重，不让龙头战法进入综合排名，不修改 API 或数据库结构，不接入自动交易，也不使用旧行情、估算价或原始价兜底。

## Capabilities

### New Capabilities

<!-- None. This change closes gaps in existing risk-control capabilities. -->

### Modified Capabilities

- `tracked-position-exit-strategy`: 在现有提醒之外持续运行无副作用的 V2 lifecycle shadow，并为退出判断记录跳空与可执行性限制。
- `etf-alert-audit`: 记录有界的 legacy/V2 shadow 对比和执行风险证据，且不把 SMTP、提醒或模拟成交升级为真实执行。
- `etf-observation-portfolio-optimization`: 增加组合级风险资产暴露上限、现金下限、相关簇以及波动/回撤风险预算。
- `etf-portfolio-backtest`: 将跳空、价差、费用和基础/压力滑点纳入可审计的模拟成交口径。
- `intraday-etf-watch`: 将缺失显式决策资格的旧报价按不可决策处理，禁止 fail-open。

## Impact

- 后端主要影响 `app.services.portfolio_allocation`、`app.services.tracked_positions`、`app.services.risk_alerts`、`app.services.workflows`、持仓调度编排和 ETF 研究回测；保持既定领域依赖方向。
- 复用已有 `exit_state_json`、alert audit context、shadow evidence 和研究结果 JSON，不新增数据库迁移或外部依赖。
- 调度仍保持单实例、串行和有界处理；V2 影子评估失败只形成聚合诊断，不影响旧提醒和盘中行情主任务。
- UI/API 现有响应保持兼容；新增证据优先进入已有结构化 context/summary，缺失时显示 unavailable，而不是构造交易结论。
