## Context

FundScope 当前 ETF 排序主要由固定窗口收益、风险扣分和流动性分数线性组合生成。这个口径简单可解释，但在全量 ETF 池里有三个问题：

- 不同主题和品种的正常波动水平不同，固定阈值会让高波动主题天然更容易高分或被惩罚。
- 多个 ETF 的流动性分、风险分经常被截到相同上限，导致最终分数聚集。
- 标签历史有效性、数据可信度、折溢价和流动性质量已经在系统里存在，但没有稳定地进入最终排序。

本设计只整理 ETF 排序评分层。它遵守项目后端领域边界：行情数据只提供可信输入，短线研究只产出评分和标签，组合配置再读取排序结果，不反向调用持仓或通知。

## Goals / Non-Goals

**Goals:**

- 把 ETF 最终排序升级为版本化 `final_score_v2`。
- 使用横截面分位比较，减少固定分数导致的同分和跨板块误判。
- 将动态阈值、标签历史有效性、数据可信度、流动性和折溢价纳入同一个 score breakdown。
- 保持旧 `total_score`、观察标签、API URL 和前端主要字段兼容。
- 确保 stale、estimated、unavailable、fallback 文本不能提高排名。
- 为后续回测和标签验证提供清晰的 `score_version` 和证据来源。

**Non-Goals:**

- 不改变邮件提醒触发条件。
- 不改变追踪持仓盈亏和仓位 sizing。
- 不接券商、不自动交易、不生成真实订单。
- 不让 AI 直接决定分数或标签。
- 不引入 Redis、Qlib、PyPortfolioOpt 等新依赖。

## Decisions

### Decision 1: 保留 `total_score`，新增版本化评分拆解

实现时继续返回旧 `total_score`，但其来源切换为 `final_score_v2`，并在 `score_breakdown_json` 中写入：

- `score_version`
- `final_score`
- `cross_sectional_percentile_score`
- `dynamic_threshold_score`
- `label_evidence_score`
- `data_reliability_score`
- `liquidity_premium_score`
- `score_confidence`
- `score_reasons`

理由：前端和现有测试依赖 `total_score`，直接删除或改名会扩大破坏面。版本化拆解能让后续回测准确知道当时使用的规则。

备选方案是新增完全独立的排序接口，但会造成页面、组合和回测继续出现两套排序口径，不采用。

### Decision 2: 横截面分位先在内存批量计算，不新增大表

信号生成时一次拿到候选 ETF 的指标，按字段批量计算分位：

- 全市场分位：用于整体强弱排序。
- 主题/资产桶分位：用于同类比较，避免半导体、债券、跨境 ETF 使用完全相同绝对阈值。
- 综合分位：默认由全市场分位和同类分位加权。

理由：当前 ETF 池约千级，批量内存计算足够，不需要引入新存储或 Redis。

备选方案是把每个分位落库成独立表，但第一版收益不大，迁移和一致性成本更高。

### Decision 3: 动态阈值只调整排序和标签信心，不跨层读取用户持仓

动态阈值输入来自已有 `dynamic_thresholds` 的波动率、资产桶、主题和历史分位信息。它可以影响：

- 追高/冲高惩罚强度。
- 回踩是否健康。
- 跌破/放量转弱惩罚。
- 波动率高低对应的合理容忍区间。

它不能读取用户持仓、邮件提醒记录或通知状态。

理由：动态阈值属于研究信号层，不能和 Position Tracking / Risk Alert 混在一起。

### Decision 4: 标签历史有效性采用有上限的加减分

标签验证结果只在满足以下条件时进入最终排序：

- 规则版本或证据 contract hash 与当前排序版本兼容。
- 样本数达到最低门槛。
- 证据状态不是 stale / insufficient。
- 使用的数据是 decision-eligible。

有效证据只做小幅加权或降权，不能单独把低质量 ETF 推到前列，也不能单独触发买卖建议。

理由：历史有效性有价值，但样本会滞后，也可能遇到市场风格切换。成熟量化项目通常把历史表现作为验证和权重输入，而不是唯一决策。

### Decision 5: 数据可信度是硬门槛，流动性/折溢价是惩罚项

数据可信度分为：

- `verified` / `alternate_provider`：可进入排序计算。
- `estimated` / `stale` / `unavailable`：只允许展示，不允许提高分数。

流动性和折溢价处理为：

- 成交额过低或成交额不稳定：降分或不可决策。
- 折溢价异常：强降分或 watch-only。
- 折溢价缺失：不直接判定不可买，但必须降低 `score_confidence`，并展示限制。

理由：金融场景宁可等待，也不能让旧行情、估算价或结构性溢价误导排序。

## Risks / Trade-offs

- [Risk] 分位排名会让整体弱市里仍然有“相对第一名”。  
  Mitigation: 前端必须展示市场状态和数据可信度；组合层仍可选择防守或等待现金。

- [Risk] 标签历史有效性样本不足时容易过拟合。  
  Mitigation: 样本不足只能显示证据不足，不能加分；近期退化只能降权或提示。

- [Risk] 评分拆解字段变多，前端可能更复杂。  
  Mitigation: 默认只展示五个维度摘要，完整 breakdown 放到展开区。

- [Risk] 旧缓存 signal item 没有 `score_version`。  
  Mitigation: 旧结果显示为“旧口径”，重新生成排序后才显示 `final_score_v2`。

- [Risk] 全量 ETF 分位计算增加信号生成耗时。  
  Mitigation: 在任务中批量计算，页面仍读缓存结果；不在页面请求时现场全池重算。

## Migration Plan

1. 增加评分版本和 score breakdown 结构，旧字段继续返回。
2. 在信号生成任务中批量计算横截面分位和动态阈值评分。
3. 接入标签历史有效性，但默认对样本不足的标签输出 `evidence_unavailable`。
4. 接入数据可信度、流动性和折溢价惩罚。
5. 前端展示新评分拆解，旧缓存显示“旧口径”。
6. 部署后重新运行 ETF 数据、信号和组合任务，生成新版本缓存。

Rollback 策略：保留旧 score_breakdown 字段兼容；如新评分异常，可临时切回旧评分函数并继续返回 `total_score`。

## Open Questions

- 折溢价数据源在所有 ETF 上是否稳定可用；不可用时第一版按“降低信心、不硬判失败”处理。
- 标签历史有效性默认加减分幅度需要通过回测校准，第一版应使用保守上限。
