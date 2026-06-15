## Why

FundScope 当前短线 ETF 研究只覆盖手工维护的几十只样本，容易漏掉新上市、高活跃或主题更匹配的场内 ETF。用户已经有证券账户，短线主入口应从“小样本观察”升级为“全量 ETF 入库、质量筛选后展示、AI 辅助解释”的研究台。

## What Changes

- 将场内 ETF 宇宙从静态默认池扩展为动态数据库池，支持从公开数据源刷新全量交易所 ETF。
- 全量 ETF 入库并参与基础数据健康检查，但 `/short-term` 默认只展示质量合格 ETF，避免低流动性、样本不足、数据滞后的标的干扰新手判断。
- ETF 日线同步改为分批、可恢复、可报告失败的任务，优先更新持仓、默认精选和高成交额 ETF。
- 短线评分继续使用趋势、回撤、波动和成交额，并增加数据质量、重复主题/指数去噪和默认展示筛选。
- 引入规则版“今日观察组合”，借鉴目标权重思想输出 ETF 权重和现金比例，仍只用于研究和手动交易参考。
- AI 研究说明扩展为技术面、流动性、风险、主题方向和组合视角解释；AI 不改排名、不生成自动交易指令。

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `short-etf-research`: 扩展 ETF 宇宙来源、同步范围、默认展示筛选、动态 ETF 详情、AI 解释和目标权重式观察组合。

## Impact

- Backend: ETF 宇宙发现服务、短线研究同步任务、ETF 评分筛选、短线研究 API、任务结果和测试。
- Frontend: `/short-term` ETF 模式增加全量统计、默认精选/全部可分析筛选、数据质量提示、观察组合展示。
- Data: 扩展 `tradable_etfs` 使用方式，继续复用 `etf_price_history`、`etf_data_health`、`short_research_signal_runs/items`。
- Safety: 不接券商、不自动下单、不输出买卖指令；低质量 ETF 必须显示风险或从默认榜单排除。
