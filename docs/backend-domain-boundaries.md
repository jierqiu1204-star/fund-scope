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

## 依赖守则

- 行情层不得 import `short_research`、`tracked_positions`、`notifier`。
- 通知层不得 import 行情、研究、持仓。
- 组合层不得 import 用户持仓或通知。
- 需要串联多个领域时，放到 `scheduler`、`admin` 或 `workflows`，不要塞回底层服务。
