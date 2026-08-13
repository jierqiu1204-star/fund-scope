## Why

现有个股龙头战术能够判断主题热度、同主题上涨广度和个股核心度，但没有表达“龙头仍强而中位梯队已集体转弱”的情绪周期背离。直接把文章经验写入选股分数会混淆 alpha 与风控，因此需要增加一个独立、可追溯、研究态的风险覆盖层。

## What Changes

- 为 A 股龙头战术增加透明的中位梯队走弱代理，基于信号截止时间前可见的复权日线、PIT 主题和既有核心度分层计算市场广度风险。
- 输出 `healthy`、`warning`、`risk_off`、`unavailable` 四种风险状态，以及 `new_entry_allowed`、`observe_only` 两种研究动作模式。
- 风险状态不修改原始公式得分、`qualifies` 或生命周期；仅对 A 股 `leader_breakout_proxy_v2` 的新开仓语义进行覆盖，其他候选保留原始研究结果并披露风险事实。
- 将风险指标、阈值、可用样本、数据截止时间、公式 hash 和稳定原因写入候选证据与只读 API，并在龙头战术页面展示。
- 对涨停高度、晋级率和炸板率缺少事实型 PIT 数据的情况 fail closed，明确标记为未接入，不从复权日线反推。
- 先以 `policy_shadow` 验证风险覆盖层；不得自动影响正式排名、真实持仓、邮件、执行或 ETF 综合排名。

## Capabilities

### New Capabilities
- `ashare-leader-sentiment-risk-gate`: 定义 A 股中位梯队走弱代理、风险状态和研究动作覆盖规则。

### Modified Capabilities
- `dual-universe-leader-tactics-screen`: A 股候选需要携带独立的风险覆盖状态，同时保持原始选股和 ETF 语义不变。
- `dual-universe-leader-tactics-evidence`: 证据接口需要返回风险指标、契约身份、可用性和动作 provenance。
- `dual-universe-leader-tactics-validation`: 风险覆盖层需要在 PIT、成本后样本外比较中单独验证，不得依据同一批结果调参。

## Impact

- 影响 `app.services.strategy_lab` 中的 A 股龙头战术纯计算、物化证据和只读 API 投影，以及对应前端研究面板。
- 不新增生产交易依赖，不调用行情 provider，不修改数据库结构，不改变 ETF 龙头战术、ETF 综合排名或任何生产写链路。
- 增加纯函数、契约/边界、API 和前端交互测试；相关命令保持有界运行。
