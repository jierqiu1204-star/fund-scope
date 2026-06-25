## Why

当前 ETF 观察组合主要从综合分最高的 ETF 中取前几只并分配权重，导致“高位观察 / 高位别追 / 冲高别追”的 ETF 也会进入组合主位。对短线研究来说，这会把“值得盯”误读成“现在适合买”，不符合谨慎金融产品的表达。

## What Changes

- 将 ETF 观察组合从“高分 Top N 等权近似”改为“买点合格候选 + 风险约束 + 现金保留”的研究参考。
- `高位观察`、`高位别追`、`冲高别追`、`跌破等待`、`放量转弱`、`数据不足` 不再进入主观察组合权重。
- 新增组合分层展示：`观察组合`、`强势但别追`、`等待/回避`。
- 借鉴 PyPortfolioOpt 的组合优化思想，但第一版不引入新依赖：使用现有日线收益、波动、回撤、相关性和主题方向做简化约束。
- 保留研究-only 文案，不输出买入指令，不连接券商，不自动下单。

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `short-etf-research`: ETF observation portfolio selection and target weights must account for entry timing, overextension labels, correlation, theme concentration, volatility, and cash retention.

## Impact

- 后端：调整 `/api/short-research/observation-portfolio` 的筛选、分层和权重计算逻辑。
- 前端：`/short-term` 的 ETF 观察组合区域显示主组合、强势但别追、等待/回避三类。
- 数据：复用现有 ETF 日线、metrics、risk flags、entry timing、theme tags，不新增数据库表。
- 依赖：第一版不引入 PyPortfolioOpt；仅借鉴组合约束和风险分散思路。
