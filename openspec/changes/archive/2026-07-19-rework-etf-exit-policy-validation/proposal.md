## Why

ETF 退出信号现在被混在同一个“预测成功率”口径里评价，导致硬止损、移动止盈、趋势转弱这些本来职责不同的规则都被误读成“卖出后必须继续跌”。最近的验证结果显示部分信号命中率很低，说明需要重做证据口径，而不是继续微调一条固定线。

## What Changes

- 新增 ETF 退出策略验证口径，把信号拆成保险止损、盈利保护、趋势警戒、组合/标签退出和研究-only 状态。
- 为不同信号使用不同 KPI：硬止损看尾部亏损和最大回撤改善，移动止盈看利润保护和错过上涨，趋势转弱看 guard 后是否减少误卖。
- 新增“策略可用性结论”：`可用于邮件`、`仅网页警戒`、`研究不足`、`旧口径结果`、`候选待批准`。
- 保留现有实时邮件规则；本变更只改证据链和验证输出，不自动启用优化参数。
- 对接成熟项目思想：参考 Freqtrade Hyperopt 的参数搜索、Backtrader/QuantConnect 的 trailing stop 状态机、vectorbt 的批量止损参数验证。

## Capabilities

### New Capabilities
- `etf-exit-policy-validation`: ETF 退出信号按职责分类验证、输出分信号 KPI、证据状态和可用性结论。

### Modified Capabilities
- `tracked-position-exit-strategy`: 退出策略展示和证据 SHALL 区分真实邮件规则、guard-only 状态和研究-only 候选参数。

## Impact

- 后端：`risk_alerts`、`tracked_positions`、ETF 退出可信度/调参服务、策略证据 API。
- 前端：`/short-term/evidence` 策略证据页，持仓卡片里的退出证据说明。
- 数据：优先复用现有 JSON 结果字段；如需要新增结构，保持向后兼容。
- 运维：新增或调整研究任务输出，不影响盘中盯盘、真实邮件发送和用户追踪持仓。
