## Why

FundScope 已经有 ETF 短线标签、资金配置参考、回测、标签验证和持仓提醒，但这些能力仍可能各自读取不同数据、复制相似规则或展示不同口径。金融研究场景需要建立一条可审计闭环：页面看到的标签和组合，必须能被同一套历史回测和标签验证解释，而不是多个模块各算各的。

## What Changes

- 新增 ETF 研究证据契约：
  - 定义 `research_signal_contract`：买入观察标签、买点标签、分数、数据可信度、规则版本、输入数据时间。
  - 定义 `allocation_contract`：组合候选层、目标权重、现金/防守状态、约束版本、权重解释。
  - 定义 `replay_contract`：回测/事后验证必须使用的信号版本、组合版本、执行口径、费用、数据截止日期。
  - 定义 `evidence_summary`：页面展示用的历史证据摘要，包含样本数、未来 1/3/5/10 日表现、最大回撤、回测结果和限制说明。
- 明确层级边界和依赖方向：
  - Market Data 只提供行情和可信度，不引用研究、组合、追踪、提醒。
  - Research Signal 只生成标签、分数和解释，不读取用户持仓，不发送邮件。
  - Portfolio Allocation 只消费研究信号和行情可信度，不读取用户追踪持仓，不发送提醒。
  - Backtest / Validation 只消费契约快照和历史数据，不调用前端页面逻辑，不绕过组合服务复制规则。
  - Tracking / Risk Alert / Notification 不反向影响研究信号和组合权重。
  - Scheduler / Admin / API 只做编排，不放核心算法。
- 增加证据链状态：
  - 页面能显示当前 ETF 标签/组合是否已有同源回测和标签验证证据。
  - 如果证据缺失、样本不足或版本不一致，必须标记“等待验证/版本不一致”，不能暗示策略已经被历史验证。
- 不实现具体组合规则、不实现标签筛选 UI、不新增策略对照算法：
  - `enhance-etf-portfolio-allocation-rules` 负责组合规则。
  - `add-short-term-label-filter` 负责标签筛选 UI。
  - `add-etf-strategy-comparison-backtests` 负责策略对照回测。
  - 本变更负责把这些结果通过统一 contract 串起来。

## Capabilities

### New Capabilities
- `etf-research-evidence-contract`: 定义 ETF 短线研究从行情、信号、组合、回测、验证到展示的共享契约、版本字段和层级依赖边界。

### Modified Capabilities
- `short-term-research`: 页面必须展示证据契约状态，区分“当前信号”“组合权重”“历史验证”“回测证据”是否同源。
- `etf-portfolio-backtest`: 回测必须消费契约快照，记录并校验与短线工作台同源的规则版本。
- `etf-signal-validation`: 标签验证必须按契约中的标签、买点、信号版本和数据日期归档结果。
- `market-data-reliability`: 行情层必须提供可被契约引用的数据可信度，不允许下层使用估算/旧行情冒充可验证数据。

## Impact

- 后端：
  - 新增轻量 contract DTO / builder / validator 模块。
  - 增加禁止跨层依赖的架构测试或脚本。
  - 回测、标签验证、组合快照和短线详情需要记录/读取 contract 版本。
- 前端：
  - `/short-term` 增加证据状态展示：同源、待验证、样本不足、版本不一致。
  - 不改变榜单筛选、组合算法、邮件提醒和真实交易边界。
- 数据：
  - 优先使用 JSON 字段或已有 run/snapshot 表记录 contract，不新增复杂表。
  - 如实施阶段发现必须持久化独立 evidence run，可用向后兼容 migration。
- 风险：
  - 本变更只建立证据链和边界，不保证任何策略未来有效。
  - 旧历史回测可能没有 contract，需要显示为“旧口径结果”，不能当作当前策略证据。
