## Context

当前系统已有 ETF 追踪、硬止损、移动止盈、趋势转弱、止盈观察、退出观察、盘中提醒执行回测、Hyperopt 候选参数和策略证据页面。但用户看到的“预测成功率 10% 不到”暴露了一个核心问题：系统把不同职责的退出规则放进同一套方向预测指标里评价。

成熟项目的做法更分层：
- Freqtrade Hyperopt 把 ROI、stoploss、trailing、protection 作为可优化空间，并用 loss function 比较历史表现。
- Backtrader 和 QuantConnect 的 StopTrail / trailing stop order 是状态机，不预测最高点，只在价格创新高后上移保护线。
- vectorbt 支持批量验证 `sl_stop`、`sl_trail`、`tp_stop`，适合看参数矩阵在历史路径里的表现。

FundScope 的边界仍保持模块化单体：Market Data -> Research Signal -> Portfolio Allocation -> Position Tracking -> Risk Alert -> Notification。验证模块不得发送邮件、不得修改持仓。

## Goals / Non-Goals

**Goals:**
- 将 ETF 退出信号按职责分类验证。
- 为每类信号输出专属 KPI 和中文结论。
- 将“预测未来下跌”改成“是否达到该规则职责”。
- 让策略证据页能解释为什么某个信号不适合发邮件或只适合网页警戒。
- 保持实时邮件规则不变，候选参数默认 research-only。

**Non-Goals:**
- 不接券商、不自动交易、不自动下单。
- 不让 AI 决定止损/止盈线。
- 不自动批准 Hyperopt 候选参数。
- 不重做 ETF 买入排序和组合权重。
- 不引入 Freqtrade、Backtrader、vectorbt 作为运行时依赖。

## Decisions

### Decision 1: 信号职责分类优先于统一成功率

退出信号分为：
- `insurance_stop`：硬止损，评价极端亏损是否被截断。
- `profit_protection`：移动止盈和止盈观察，评价最高盈利保护和回吐减少。
- `trend_guard`：趋势转弱，默认 guard-only，评价是否降低误卖和阻止加仓。
- `portfolio_exit`：退出观察，评价组合/标签层风险是否需要降暴露。
- `research_only`：候选参数或旧口径证据，不进入邮件。

替代方案是继续用“触发后 1/3/5/10 日是否下跌”评价所有信号；这个方案会把保险止损误判成预测失败，因此拒绝。

### Decision 2: KPI 按职责定义

硬止损 KPI：最大亏损改善、尾部亏损减少、止损后反弹率、重复止损次数。

移动止盈 KPI：最高盈利保留率、回吐减少、错过后续上涨、提前卖飞率、执行后收益差。

趋势转弱 KPI：单独卖出误触发率、作为 guard 后减少回撤、阻止加仓后的机会成本。

退出观察 KPI：标签退化后组合暴露下降是否改善风险收益。

### Decision 3: 证据状态必须严格

证据输出必须包含：
- `policy_class`
- `evidence_status`
- `sample_count`
- `confidence`
- `decision_eligible`
- `live_rule_impact`
- `recommended_usage`

如果 contract hash 不匹配、样本不足、执行模型不一致或使用旧口径，前端只显示“旧口径/等待验证”，不得展示成当前策略有效。

### Decision 4: 参数优化只产出候选

Hyperopt 或参数矩阵搜索的结果只进入候选证据。只有人工批准且 contract 匹配后，才能被实时 `risk_alerts` 读取。默认仍使用当前动态规则。

## Risks / Trade-offs

- [Risk] 证据维度变多，用户难理解 → 用中文分组：保险止损、盈利保护、趋势警戒、组合退出。
- [Risk] 样本不足导致结论不稳定 → 低样本必须显示“研究不足”，不输出强结论。
- [Risk] 优化参数过拟合 → 要求样本外/滚动验证，并默认不自动生效。
- [Risk] 短期内看起来“系统更保守” → 金融场景宁可等待证据，也不输出错误信号。
- [Risk] 与现有未完成变更重叠 → 本变更只做证据重构和展示，不改实时阈值生效规则。

## Migration Plan

1. 先新增证据分类 helper 和输出结构，兼容旧 JSON。
2. 将现有退出可信度/Hyperopt 结果映射到新 policy class。
3. 策略证据页展示新分类和 KPI；旧口径结果标记为旧口径。
4. 后台任务继续可跑，但不改真实提醒。
5. 如发现问题，回滚前端展示和 helper，不影响已有提醒链路。
