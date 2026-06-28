# FundScope 项目级规则

## 通用执行
- Windows 上优先使用 PowerShell 7：`C:\Program Files\PowerShell\7\pwsh.exe -NoLogo -NoProfile -Command ...`。
- 修改前先明确目标和成功标准；不确定时先问，不默默猜。
- 只改和当前任务直接相关的代码，不顺手重构无关模块。
- 金融场景宁可显示“暂无/等待数据”，不要用旧行情、估算价或 fallback 数据输出误导性结论。
- 同一个错误遇到两次，不要死磕；先查 3-5 种解法，再选最小可行修复。

## 后端领域边界
FundScope 后端采用模块化单体。第一阶段不拆微服务、不改 API、不改数据库，通过服务边界和依赖测试保证依赖方向清晰。详细说明见 `docs/backend-domain-boundaries.md`。

固定依赖方向：

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

### 模块职责
- `app.services.intraday_etf`：只负责 ETF 盘中行情的拉取、写入、查询。
- `app.services.market_data`：行情只读门面，统一提供 ETF 实时快照、ETF 日线、基金净值和数据可信度。
- `app.services.short_research`：只负责短线评分、标签、买点、AI/规则说明。
- `app.services.portfolio_allocation`：只负责 ETF 组合配置、进攻/防守/等待现金、单只权重上限等规则。
- `app.services.tracked_positions`：只负责用户持仓、买入价、份额、盈亏、最高盈利和回吐。
- `app.services.risk_alerts`：只负责硬止损、移动止盈、趋势转弱、止盈观察、退出观察和仓位 sizing 纯规则。
- `app.services.notifier`：只负责 SMTP、邮件模板和发送记录，不判断该不该买卖。
- `app.services.workflows`：负责跨领域编排，例如“拉行情 -> 更新实时榜单 -> 检查追踪持仓 -> 生成提醒 -> 发送通知”。

### 禁止依赖
- 行情层不得 import `short_research`、`tracked_positions`、`notifier`。
- 研究层不得 import `tracked_positions`、`notifier`。
- 组合层不得 import 用户持仓或通知。
- 通知层不得 import 行情、研究、持仓。
- 需要串联多个领域时，放到 `scheduler`、`admin` 或 `workflows`，不要塞回底层服务。

### 验证要求
- 改动后优先跑相关测试；涉及领域边界时必须跑：
  - `uv run pytest tests/test_backend_domain_boundaries.py`
  - `uv run ruff check .`
