## Context

FundScope 的 ETF 短线链路已经在 `app.services.short_research` 中完成技术评分、横截面归一化、主题催化合成和信号 run 缓存。详情页现在已经改为读取最新 ETF 信号 run，避免单只 ETF 现场重算产生假分数。

当前缺口是“同主题板块是否整体走强”没有进入综合关注分。机器人、创新药这类 ETF 的关注度不仅取决于单只 ETF 技术形态，也取决于同板块 ETF 是否普遍上涨、是否站上均线、成交是否放大。这个信息属于 Research Signal 层，不属于原始行情层，也不应直接进入组合或持仓。

## Goals / Non-Goals

**Goals:**

- 新增 `sector_trend_score`，表达同主题 ETF 的板块趋势强度和覆盖度。
- 将综合关注分拆成可解释组件：`technical_score`、`sector_trend_score`、`catalyst_score`、`sentiment_heat_score`。
- 支持机器人和创新药作为明确主题筛选、板块趋势分组和综合关注比较对象。
- 缺少板块趋势或主题催化数据时返回 `null` 和缺失原因，不输出 fallback 分数。
- 详情页、榜单和 `sort=opportunity` 使用最新信号 run 中缓存的真实分数。

**Non-Goals:**

- 不接入外部 AI 自动调分。
- 不接入实时新闻、社交舆情或付费研报源。
- 不改变买点、风险标签、组合权重、持仓追踪、邮件提醒或自动化风控。
- 不把板块趋势当成收益预测，也不输出买入、卖出、目标价或保证收益。
- 不为 v1 新增板块趋势数据库表；先将结果写入 signal item JSON。

## Decisions

### 1. 板块趋势放在 Research Signal 层

新增 `app.services.short_research.sector_trends` 或等价模块。它读取已经计算出的 ETF signal candidates 和主题分类，产出每个主题的板块趋势快照，再回填到每只 ETF 的 metrics 和 score breakdown。

理由：板块趋势是研究加工结果，不是原始行情。放在行情层会污染 `market_data` 的职责；放在组合层会绕过短线研究证据链。

备选方案是在 API 层临时聚合，但详情页、榜单和缓存 run 会不一致，因此不采用。

### 2. v1 不新增表，缓存到 signal item JSON

板块趋势结果写入信号 item 的 `metrics_json` / `score_breakdown_json`：

- `sector_trend_score`
- `sector_trend_label`
- `sector_trend_summary`
- `sector_trend_reason`
- `sector_peer_count`
- `sector_trend_status`
- `opportunity_score`
- `opportunity_score_version`
- `opportunity_breakdown`

理由：当前详情页已经以最新信号 run 为评分可信来源；先复用该缓存可以保持榜单和详情一致，并避免数据库迁移。

备选方案是新增 `etf_sector_trend_snapshots` 表。它适合后续做趋势历史曲线和板块排行，但不是 v1 必需。

### 3. 板块趋势按主题分组后横截面计算

每只 ETF 先通过现有 `theme_profile.primary_theme` 和 `theme_group` 归类。v1 优先使用 `primary_theme`；当主主题缺失时回退到 `theme_group`；仍缺失则该 ETF 不参与板块趋势。

每个主题聚合以下指标：

- 同主题 ETF 数量和有效样本数。
- 5 日、20 日收益的中位数或均值。
- 站上 MA5 / MA20 的比例。
- 20 日成交额相对 60 日成交额的放大情况。
- 同主题 ETF 技术分均值。
- 数据滞后、数据不足、低流动性样本比例。

当有效样本不足、主题过宽或样本多数数据不可用时，`sector_trend_score` 为 `null`，并写入 `sector_trend_status = unavailable`。

### 4. 综合关注分按可用证据合成

默认口径：

```text
opportunity_score =
  technical_score * 0.60
  + sector_trend_score * 0.20
  + catalyst_score * 0.15
  + sentiment_heat_score * 0.05
```

当主题催化不可用但板块趋势可用：

```text
opportunity_score =
  technical_score * 0.75
  + sector_trend_score * 0.25
```

当板块趋势不可用但主题催化可用：

```text
opportunity_score =
  technical_score * 0.70
  + catalyst_score * 0.20
  + sentiment_heat_score * 0.10
```

当板块趋势和主题催化都不可用：

- `opportunity_score = null`
- `opportunity_label = 暂无综合关注`
- `sort=opportunity` 将该 ETF 排到有真实综合关注分的 ETF 后面。

风险标签仍是硬约束。`冲高别追`、`数据不足`、`数据滞后`、`流动性不足` 等状态不能被板块趋势或催化清除。

### 5. 机器人和创新药主题覆盖

机器人：

- 从“人工智能”规则中拆出更精确的机器人匹配。
- 主题关键词覆盖 `机器人`、`具身智能`、`人形机器人`、`工业机器人`。
- 复用已有机器人主题催化事件。
- 默认候选通过 ETF 元数据/名称关键词加入；已在服务器数据中的机器人 ETF 应进入主题分组。

创新药：

- 从泛“医药/生物医药”中拆出创新药匹配。
- 主题关键词覆盖 `创新药`、`生物药`、`生物医药`、`港股创新药`、`医药创新`。
- 没有真实催化事件时，不创建假催化快照；仅展示板块趋势和技术分。

### 6. API 和前端明确显示数据状态

API 对外新增字段或通过现有 `metrics` / `score_breakdown` 暴露：

- `sector_trend_score`
- `sector_trend_label`
- `sector_trend_summary`
- `sector_peer_count`
- `sector_trend_status`
- `opportunity_score_version`
- `opportunity_breakdown`

前端只在字段为数字时格式化为分数；字段为 `null` 时显示“暂无”。综合关注分旁显示口径，例如“技术+板块趋势”或“技术+板块趋势+主题催化”。

## Risks / Trade-offs

- [Risk] 小样本主题的板块趋势容易失真。  
  Mitigation: 输出 `sector_peer_count` 和 `sector_trend_status`，样本不足时不打分。

- [Risk] 板块趋势和单只 ETF 技术分高度相关，可能重复加分。  
  Mitigation: 板块趋势权重限制在 20%-25%，并在 breakdown 中展示来源。

- [Risk] 创新药 ETF 数量和名称口径可能混杂医药、医疗、生物药、港股创新药。  
  Mitigation: v1 以关键词和主题归类为准，详情页展示分类来源和置信度；后续如需精确持仓成分再另开任务。

- [Risk] 用户可能把综合关注分理解为买点。  
  Mitigation: UI 保持“综合关注/观察”文案，买点标签和风险标签独立显示。

- [Risk] 生成信号时计算板块趋势会增加一点耗时。  
  Mitigation: 使用本次 run 已经计算好的 metrics 聚合，不额外逐只读取日线。

## Migration Plan

1. 新增板块趋势计算模块和单元测试。
2. 调整主题分类规则，补齐机器人和创新药独立主题。
3. 在 ETF 信号生成链路中插入板块趋势回填，再合成新版综合关注分。
4. 扩展 API schema 和 `_asset_out` 输出，保证缺失值为 `null`。
5. 更新 `/short-term` 列表、详情页和主题筛选展示。
6. 增加排序、缓存详情一致性、缺失数据和前端展示测试。
7. 部署后运行 ETF 信号生成任务，手动验收机器人、创新药、半导体、光模块和无主题 ETF。

Rollback：保留旧技术分和旧主题催化字段；如果板块趋势计算失败，API 返回 `sector_trend_score = null`，旧排序和详情页仍可工作。

## Open Questions

- 是否需要在 v2 新增板块趋势历史表，用于展示板块趋势曲线和回测。
- 创新药是否要拆分 A 股创新药、港股创新药和生物药，v1 暂时统一为“创新药”。
- 机器人默认 ETF 候选是否只从服务器数据库现有 ETF 动态匹配，还是同时维护少量固定默认代码；v1 推荐优先动态匹配，避免硬编码过期清单。
