# 后端领域边界

FundScope 后端采用模块化单体。第一阶段不拆微服务、不改 API、不改数据库，通过服务边界和依赖测试先把方向固定下来。

允许的主链路依赖方向：

```text
Market Data 行情数据
  ↓
Research Signal 研究信号
  ↓
Portfolio Allocation 组合配置
  ↓
Position Tracking 持仓追踪
  ↓
Risk Alert 风控提醒
  ↓
Notification 通知发送

Scheduler / Admin / API 只做编排
Strategy Lab 只复用数据和研究结果，不反向影响日常短线链路
```

## 模块职责

- `app.services.intraday_etf`：拉取、写入、查询 ETF 盘中行情；不判断用户持仓，不发送提醒。
- `app.services.market_data`：行情只读门面，供研究、持仓、组合统一取行情。
- `app.services.short_research`：短线评分、标签、买点、AI/规则说明。
- `app.services.portfolio_allocation`：ETF 组合配置规则、权重上限、进攻/防守/等待现金口径。
- `app.services.tracked_positions`：用户持仓、买入价、份额、盈亏、最高盈利和回吐。
- `app.services.risk_alerts`：提醒类型、风控信号和仓位 sizing 纯规则。
- `app.services.notifier`：SMTP、邮件模板、发送记录；不判断该不该买卖。
- `app.services.workflows`：跨领域编排，例如“盘中拉行情后检查已追踪持仓提醒”。
- `app.services.etf_research_evidence`：ETF 研究证据契约，只定义版本、hash、证据状态和轻量 DTO，不读取数据库，不调用业务服务。

## 依赖守则

- 行情层不得 import `short_research`、`tracked_positions`、`notifier`。
- 通知层不得 import 行情、研究、持仓。
- 组合层不得 import 用户持仓或通知。
- 需要串联多个领域时，放到 `scheduler`、`admin` 或 `workflows`，不要塞回底层服务。

## 已知遗留边界

当前 `tracked_positions.service` 仍包含部分短线研究查询和邮件发送编排，这是历史实现遗留。本次证据契约变更不做大搬家；后续应逐步迁到 `risk_alerts` 和 `workflows`，再扩大依赖检测范围。当前强制检测先覆盖行情层、研究层、组合层和通知层，防止新的反向依赖继续扩散。

## ETF 研究证据链

ETF 短线页、组合、回测和标签验证必须通过证据契约说明是否同源。契约包含：

- `research_signal_contract`：信号日期、标签、买点、分数、数据可信度、规则版本。
- `allocation_contract`：组合模式、目标权重、约束、数据时间和来源信号版本。
- `replay_contract`：回测/验证的信号版本、组合版本、执行模型、费用模型和数据截止日期。
- `evidence_summary`：样本数、覆盖率、回测指标、限制说明和证据状态。

没有 contract hash 的旧模拟盘、旧回测和旧验证结果只能显示为“旧口径结果”，不能当作当前 ETF 工作台策略的历史证明。回测和验证只产出研究证据，不得自动修改实时排序、组合权重、追踪持仓或邮件阈值。
