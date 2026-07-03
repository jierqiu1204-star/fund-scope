## Context

FundScope 当前已有 ETF 盘中提醒、退出信号可信度统计、动态止损/止盈线、盘中执行回测和 ETF 研究证据契约。最近的可信度验证说明，`take_profit_watch` 与 `trailing_take_profit` 的历史表现相对更好，但 `hard_stop`、`trend_weakening`、`exit_watch` 的误触发率偏高，不能继续只依赖经验阈值。

成熟项目的共同做法不是让 AI 直接预测最高点，而是将退出参数作为可搜索空间，反复回测并用样本外验证约束过拟合。Freqtrade Hyperopt 适合作为退出参数搜索的主参考；vectorbt/Backtrader 适合作为止损、移动止盈和回放验证思想参考；QuantConnect 的风险模型分层适合保持“组合目标”和“风控调整”分离；PyPortfolioOpt 更适合组合权重，不直接决定退出时机。

本项目已有后端领域边界，新增模块必须遵守：

```text
Market Data -> Research Signal -> Portfolio Allocation -> Position Tracking -> Risk Alert -> Notification
Scheduler / Admin / API / Workflows 编排跨领域流程
```

## Goals / Non-Goals

**Goals:**
- 新增 ETF 退出参数校准模块，按 ETF 类型/板块/波动率分桶搜索候选参数。
- 对每组参数输出样本内、样本外、滚动验证、误卖率、错过上涨率、邮件次数、换手和证据质量。
- 支持人工批准参数后，`risk_alerts` 使用批准参数计算实时退出线。
- 当前默认规则继续可用；没有批准参数时不改变真实邮件提醒链路。
- 调参任务研究只读，不创建真实提醒、不发送邮件、不修改持仓。

**Non-Goals:**
- 不接券商、不自动交易、不自动下单。
- 不训练机器学习模型，不用 AI 生成参数。
- 不把候选参数自动应用到实时提醒。
- 不改现有短线排序、组合权重、买点标签算法。
- 不引入 Freqtrade/vectorbt/PyPortfolioOpt 作为强依赖；第一版实现项目内轻量参数搜索。

## Decisions

### Decision 1: 先做项目内轻量 Hyperopt，不引入外部交易框架

第一版实现一个可控的网格/随机搜索器，而不是直接引入 Freqtrade 或 vectorbt。

理由：
- 当前项目只做 A 股 ETF 研究和邮件提醒，直接接入完整交易框架会扩大部署和依赖风险。
- 服务器资源有限，搜索空间需要可控。
- 我们已经有盘中行情、日线、真实提醒规则和执行回测，缺的是参数搜索与样本外验证。

替代方案：
- 直接引入 Freqtrade：功能强，但交易所/数据格式适配成本高。
- 直接引入 vectorbt：批量回测能力强，但需要重构数据矩阵和回测接口。

### Decision 2: 参数分桶优化，而不是全市场一套线

校准模块按 ETF 类型、主题/行业标签、波动率分位进行分桶。每个桶独立搜索：
- `hard_stop_multiplier`
- `profit_start_multiplier`
- `trailing_giveback_multiplier`
- `trend_confirm_days`
- `take_profit_watch_pct`

理由：
- 半导体、人工智能、银行、黄金、债券、跨境 ETF 的波动行为不同。
- 统一阈值会导致低波品种过松、高波品种过紧。

替代方案：
- 每只 ETF 单独优化：样本容易不足，过拟合严重。
- 全市场统一优化：稳定但不够贴合品种特性。

### Decision 3: 样本外验证优先于样本内最优

每个候选参数必须记录训练区间、样本外区间和滚动窗口结果。默认目标函数不是单纯收益最大化，而是稳健优先：

```text
score =
  回撤控制收益
  + 收益/回撤比
  + 有效保护得分
  - 误卖后上涨惩罚
  - 邮件频率惩罚
  - 换手惩罚
  - 样本不足惩罚
  - 样本外退化惩罚
```

理由：
- 用户需要的是少误导、可解释、可复核的提醒，不是历史收益最高的参数。
- 样本内好、样本外差是调参最常见风险。

### Decision 4: 候选参数不自动生效

校准结果默认 `candidate`。只有人工批准后才变成 `approved` 并可供 `risk_alerts` 读取。

理由：
- 金融场景不能让夜间优化任务直接改变第二天真实邮件规则。
- 用户需要看到证据后再决定是否采用。

生效边界：
- `etf_exit_calibration` 写候选结果。
- `risk_alerts` 读取已批准参数快照。
- `notifier` 不知道参数来源，只负责发送。

### Decision 5: 可信度校准使用保守收缩

退出信号可信度不能只看原始胜率。低样本高胜率需要向全局平均收缩，避免“少数样本刚好表现好”被误当成可靠。

第一版采用可解释的经验贝叶斯/Beta-binomial 风格校准：

```text
calibrated_rate = (success_count + prior_success) / (sample_count + prior_total)
```

prior 使用同信号类型或全局退出信号的长期样本。

### Decision 6: 校准结果纳入 ETF 研究证据契约

每次校准运行记录：
- signal_rule_version
- exit_rule_version
- calibration_rule_version
- data_cutoff
- data_window
- execution_model
- contract_hash

如果当前页面策略、回测、可信度和校准结果不是同一 contract，前端必须显示“旧口径/等待验证”。

## Risks / Trade-offs

- 参数过拟合 → 强制样本外验证、滚动验证、最小样本数和过拟合惩罚。
- 服务器资源占用高 → 分桶增量运行，限制搜索空间和每日最大组合数。
- 样本不足导致错误结论 → 标记 `evidence_insufficient`，不允许批准。
- 优化后邮件变多 → 目标函数惩罚邮件次数和换手。
- 用户误解候选参数已生效 → 前端分开展示“当前生效参数”和“候选参数”。
- 外部行情覆盖不足 → 只使用 verified / alternate_provider 数据；缺盘中历史时不回退日线冒充盘中校准。

## Migration Plan

1. 新增校准结果表和索引，不影响现有追踪和提醒表。
2. 部署后先以只读研究模式运行，生成 `candidate`。
3. 页面展示候选结果，但实时邮件规则继续使用当前规则。
4. 如需启用候选参数，后续通过单独变更添加人工批准入口或数据库批准流程。
5. 回滚时删除/忽略候选参数，不影响当前 `risk_alerts` 默认规则。

## Open Questions

- 第一版搜索频率是否每天全量运行，还是只对最近有追踪/高排名的板块运行？
- 候选参数人工批准入口放在策略证据页，还是后台任务页？
- 样本外窗口默认取最近 20、40 还是 60 个交易日？
- ETF 类型/主题标签如果缺失，默认归入“unknown”还是跳过校准？
