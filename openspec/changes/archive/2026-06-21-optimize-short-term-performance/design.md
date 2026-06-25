## Context

线上只读检查显示，当前最大表为 `short_research_signal_items` 和 `etf_intraday_quotes`，均约 40MB，数据库体量本身还不大。主要瓶颈来自查询路径：盘中实时榜单会加载被盯盘 ETF 的全部历史盘中行情再在 Python 中取最新值；“我的持仓”会按持仓逐条重复查询最新价、短线 run、signal items、advisor reports、走势图和最近提醒。

## Goals / Non-Goals

**Goals:**

- 让 `/short-term` ETF 模式首屏、实时榜单、我的持仓在盘中轮询时保持稳定低延迟。
- 避免 `etf_intraday_quotes` 随盘中明细增长拖慢最新行情读取。
- 保留金融判断所需的日线历史、盘中日汇总和持仓高水位状态，清理原始盘中明细不影响标签与提醒。
- 用 PostgreSQL 原生能力完成优化，不引入 Redis、TimescaleDB 或新的任务队列。

**Non-Goals:**

- 不改变短线评分、买点标签、观察组合权重、卖出提醒阈值。
- 不改变邮箱提醒触发条件，不新增买入提醒。
- 不接券商、不接支付宝、不做自动交易。
- 不重构整个 `/short-term` 页面，只优化必要的数据路径。

## Decisions

1. **最新盘中行情用 SQL 单条读取，而不是 Python 去重。**  
   `latest_quotes_by_code` 改为 `LATERAL` 子查询或等价窗口查询：从 watch codes 出发，每个 `etf_code` 按 `quote_time DESC, id DESC LIMIT 1` 取一条。线上 EXPLAIN 显示该思路可从约 115ms 降到约 2ms。备选 `DISTINCT ON` 在当前索引下仍会扫描大部分表，因此不采用。

2. **补充查询排序索引。**  
   新增 migration 覆盖最近成功短线 run、最新任务记录、盘中最新行情、最近盘中提醒、止盈冷却查询。已有 `(code,date)` 日线索引保留，不重复创建同义索引。

3. **追踪持仓列表批量化。**  
   列表接口一次加载当前用户持仓后，批量读取最新提醒、最近盘中提醒、最新短线 signal item、advisor report、最新价格和必要图表数据。详情接口仍可加载完整图表和全部提醒，列表只返回卡片所需字段。

4. **盘中原始明细保留 60 个交易日。**  
   `etf_intraday_quotes` 作为热数据表，只保留近 60 个交易日。长期分析依赖 `etf_price_history`、短线 signal 缓存、持仓 alert 记录和新增/复用的盘中日汇总。若无盘中日汇总表，则新增轻量表按 `etf_code + trade_date` 保存当日最高/最低/最后价、最大盘中回撤、成交额、行情条数和数据源。

5. **首屏接口减少重复请求但保持兼容。**  
   优先优化现有接口；如仍有明显重复，可新增 `/api/short-research/workbench` 聚合接口返回状态、第一页榜单、观察组合摘要和持仓摘要。旧接口继续可用。

6. **清理死代码只限性能相关路径。**  
   删除 `signal_run_items_as_assets` 中 `return` 后不可达代码，不做无关重构。

## Risks / Trade-offs

- **清理盘中明细后无法回看分钟级旧行情。** → 保留 60 个交易日原始明细，并长期保留盘中日汇总；持仓高水位状态写入持仓/提醒记录。
- **批量化接口可能一次改动较多。** → 先保持响应模型不变，逐步替换内部查询；新增测试覆盖列表字段一致性。
- **新增索引会增加写入成本。** → 只加覆盖当前热查询的少量复合索引，避免 JSON 字段索引和宽索引。
- **保留策略误删当日数据会影响盘中提醒。** → 清理任务只删除 `trade_date < cutoff_date`，不按 `quote_time` 删除当前交易日；先在测试中固定 cutoff 验证。

## Migration Plan

- 新增 Alembic migration 创建索引和盘中日汇总表（如当前不存在）。
- 部署后运行 migration，再启动服务；清理任务默认每天收盘后或夜间运行。
- 回滚时可保留新增索引/汇总表不影响旧代码；如需回滚代码，旧接口仍能读取原始行情。

## Open Questions

- 无。默认采用 60 个交易日原始盘中明细保留期。
