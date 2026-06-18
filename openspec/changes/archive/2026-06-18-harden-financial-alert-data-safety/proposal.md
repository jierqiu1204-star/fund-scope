## Why

当前短线研究和持仓提醒已经能使用公开行情、规则引擎和 AI 说明，但金融场景里不能让 fallback 数据、旧行情、网页提示或模型兜底误导成真实盘中信号。近期还发现 `take_profit_watch` 可能被历史 `web_only/suppressed` 记录挡住首次邮件，以及盘中重复提醒会每分钟写入 suppressed 噪音记录。

这次变更要把“数据可靠性”和“邮件触发资格”收紧：只有足够可靠的数据和明确持仓处理信号才能发邮件，其他情况只做网页提示；AI 只解释研究依据，不影响排序、动态线和提醒触发。

## What Changes

- 新增市场数据可靠性分级：区分新鲜盘中行情、日线收盘价、备用数据源、旧行情、缺失数据和 AI 规则兜底。
- 收紧 ETF 盘中提醒门槛：盘中强提醒只允许基于新鲜盘中行情；`daily_close`、旧行情、暂无 IOPV、结构数据不完整只做网页提示。
- 修复 `take_profit_watch` 冷却：3 天冷却只看真正发过邮件的记录，不让旧的 `web_only`、`suppressed`、`skipped` 挡住首次邮件。
- 降低盘中重复提醒写库噪音：30 分钟内重复抑制不再每分钟插入 suppressed 记录，硬止损明显恶化仍可升级提醒。
- 优化 AI 研究说明安全口径：prompt 先约束模型不要输出不当金融建议，后端继续校验；区分“系统规则名”和“交易指令”；页面展示 `AI生成`、`规则兜底`、`部分规则补齐`。
- 不改变真实交易边界：系统仍不连接支付宝、不连接券商、不自动买卖。

## Capabilities

### New Capabilities
- `market-data-reliability`: 约束行情、净值、备用源、旧行情和 AI 兜底在研究、展示和邮件触发中的使用边界。

### Modified Capabilities
- `intraday-etf-watch`: 盘中 ETF 盯盘必须基于新鲜行情触发邮件，并避免重复 suppressed 记录刷库。
- `tracked-position-exit-strategy`: 持仓离场邮件冷却和触发资格按真正邮件发送状态与数据可靠性判断。
- `llm-conservative-research-advisor`: AI 说明通过 prompt 和后端校验共同约束，减少不必要兜底并保留金融安全闸门。

## Impact

- 影响后端短线研究、盘中 ETF 盯盘、追踪持仓提醒、通知记录和 AI advisor 服务。
- 影响 `/short-term` 的数据来源、提醒状态、AI/规则说明展示文案。
- 影响后台任务和定时任务的提醒结果，但不新增外部依赖、不改数据库大结构、不改变登录或真实交易逻辑。
