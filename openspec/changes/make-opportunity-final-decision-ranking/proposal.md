## Why

`/short-term` 里的“综合关注”目前实际使用 `opportunity_score`，更偏主题热度和板块催化，容易把医药、机器人这类热主题排到前面，却没有充分体现买点、风险、数据可信度、流动性、标签历史表现等最终决策因素。用户期望“综合关注”就是各方面都考虑后的最终最有参考价值榜单，因此需要收敛排序口径。

## What Changes

- 将“综合关注”定义为 ETF 最终决策榜，排序优先使用现有 `final_score_breakdown.final_score`。
- 原 `opportunity_score` 不再作为“综合关注”的主排序依据，降级为主题/板块热度辅助字段。
- 最终分必须综合考虑横截面分位、动态阈值、标签历史有效性、数据可信度、流动性/折溢价惩罚、板块/主题热度等因素。
- `冲高别追`、`跌破等待`、`放量转弱`、数据不足等状态不得被单纯板块热度覆盖到最终榜单前列。
- 前端文案保持“综合关注”，但展示说明改为最终决策口径；主题热度只作为辅助解释。
- 部署后重新生成 ETF 分数和 ETF 资金配置参考，并重新跑 Top 5/10/20/50 历史表现验证。

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `short-term-research`: “综合关注”排序从主题催化型 `opportunity_score` 改为综合最终决策分，要求风险、买点、数据可信度和历史标签表现参与排序。

## Impact

- 后端：`app.services.short_research` 排序字段和序列化说明；必要时补充 final score 缺失时的保守 fallback。
- 前端：`frontend/app/short-term/page.tsx` 的排序说明、分数展示、辅助热度说明。
- 测试：短线研究排序 API 测试、前端类型检查。
- 运维：部署后需要重新运行 ETF 数据/信号/组合/历史表现验证任务。
