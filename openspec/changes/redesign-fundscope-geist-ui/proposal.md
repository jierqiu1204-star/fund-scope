# OpenSpec Change: redesign-fundscope-geist-ui

## What
把 FundScope 的前端视觉从暖色大卡片风格收敛为 Geist light theme 风格的投资研究工作台。第一版优先改全局壳层、共享组件和 `/short-term`，让核心短线研究页更高密度、更少装饰、更容易扫数据。

## Why
当前界面使用米色渐变、大圆角、重阴影和较长说明文案，适合展示型页面，但不适合每天看 ETF 排名、买点状态、持仓提醒和数据可信度。短线研究需要更像工具台：信息层级清楚、控件紧凑、数字优先、状态醒目但不夸张。

## Scope
- 只改前端布局、组件和样式 token。
- 保留现有 API、数据库、URL、类型字段和业务逻辑。
- 不改评分算法、邮件提醒、追踪持仓、盘中盯盘或交易边界。
- 不引入新前端依赖。

## Non-goals
- 不做深色模式。
- 不做 Vercel 品牌化，只参考 Geist 的中性灰、紧凑控件、细边框和低阴影。
- 不把未完成的 `add-multi-source-etf-quotes` 混入本变更。
