## Context

ETF 盘中盯盘当前使用单一公开行情源并把归一化后的快照写入 `etf_intraday_quotes`。页面、实时榜单和追踪提醒都依赖这条最新快照；一旦单源延迟或字段异常，用户会看到与券商平台不一致的价格，并且难以判断是否只是数据问题。

现有表已包含 `source`、`freshness_status` 和 `raw_json`，足以保存多源校验明细。第一版不做数据库迁移，避免扩大变更面。

## Goals / Non-Goals

**Goals:**
- 后端同时采集多个公开 ETF 行情源，归一化后选出一条可信主行情。
- 用源间一致性、行情时间、字段完整度和新鲜度决定是否可参与实时分数、追踪盈亏和邮件提醒。
- 前端只增加轻量数据可信度提示，继续复用现有价格、涨跌、行情时间字段。

**Non-Goals:**
- 不接券商行情 API，不保证和证券 App 完全一致。
- 不重做 `/short-term` 页面布局，不改变排序和卖出提醒算法。
- 不用估算价、旧行情或 AI 解释替代真实行情。

## Decisions

- **Provider adapter pattern:** 新增轻量 provider adapter，统一返回 `NormalizedQuote`。第一版实现当前 AKShare provider，并补充至少一个直接公开源 provider（优先东方财富直接接口；如果字段不足，再用新浪/腾讯作为补充源）。这样不引入重依赖，也便于单源失败隔离。
- **Parallel fetch with timeout:** 盘中任务并发请求各 provider，每个 provider 独立超时、退避和错误记录。一个源失败不阻塞其他源；所有源失败时沿用当前“使用最近缓存但不可决策”的口径。
- **Primary quote selection:** 只在至少一个 provider 返回有效代码、价格和非 fallback 行情时间时生成主行情。若多个源可用，优先选择最新且字段完整的来源；若价格差异超过阈值（默认 `0.3%` 或绝对价差超过 `0.003`，取更严格者），标记 `provider_diverged`，仅网页展示，不触发邮件。
- **Compatibility-first storage:** `etf_intraday_quotes.source` 保存主行情来源；`raw_json` 保存 `provider_quotes`、`provider_status`、`primary_provider`、`consensus_status`、`price_diff_pct`、`decision_eligible` 和拒绝原因。API 增加同名只读字段，旧字段保持不变。
- **Decision gating:** 实时综合分、盘中买点、追踪邮件只允许使用 `decision_eligible=true` 的主行情。分歧、过期、缺行情时间、估算或 fallback 行情均降级为显示状态。

## Risks / Trade-offs

- **公开源本身可能同源或同延迟** → 在 UI 明确显示“公开多源校验”，不承诺券商级实时。
- **多源请求拖慢每分钟任务** → provider 并发、短超时、退避；任务结果记录每个源耗时和失败原因。
- **源间小幅差异频繁导致不可决策** → 使用小但非零的容忍阈值，并在任务日志中暴露分歧数量，后续可根据真实表现微调。
- **raw_json 变大** → 仅保存每个源的关键字段和原始错误摘要，不保存完整大响应。
