## Context

生产 ETF universe 已扩展到约 1,463 只。当前 `post_close_etf_adjusted_sync_job` 在发布门槛不足时调用旧的 `sync_short_research_data`：每次选择 20 只、逐只拉取、默认在 ETF 间等待一秒，并且仅请求目标交易日。该路径可以缓慢增加当天复权覆盖，却不能高效补齐评分所需的 61 个连续可决策交易日；调度每晚只有少量切片，达到 95% 双门槛需要多日，而且门槛失败时会把完整代码列表重复写入 `JobRun.details_json`。

代码库已经包含 `run_bounded_history_sync_slice`，它提供单 worker 租约、稳定 code/date 页、批量 upsert、分页提交、identity hash、耐久 checkpoint、provider deadline、行数/RSS/SQL 上限以及 60 秒退出保证。发布路径没有复用该实现，`daily_freshness` lane 也只出现在 readiness 投影中，没有实际续跑入口。

部署目标为 2 核 4 GB。金融数据规则要求宁可显示等待，也不能使用 Sina/efinance 原始价格、盘中快照、估算价或旧快照代替 `total_return_adjusted` 决策数据。当天决策数据和评分合格数据的发布门槛均保持 95%。

## Goals / Non-Goals

**Goals:**

- 让 1,400 量级 ETF universe 在单 worker、60 秒硬上限和有限内存内，于一个收盘后追赶窗口中持续、可恢复地接近 95% 发布门槛。
- 一次有限 provider 请求同时补充目标交易日和 61 交易日暖机窗口，减少重复网络往返，但分别核算并持久化两个 lane 的事实覆盖。
- 统一日常发布恢复和历史续跑的断点、租约、分页 upsert、资源遥测和失败语义。
- 避免阻塞 provider、固定 sleep、重复 TLS 建连和大型 JobRun JSON 消耗大部分切片预算。
- 达到双门槛后只生成和发布一次可验证双榜单快照。

**Non-Goals:**

- 降低 95% 决策数据或评分覆盖门槛。
- 使用 Sina/efinance 原始价格、盘中收盘补造、估算值或旧快照增加覆盖。
- 增加多 ETF 并发 worker、拆分微服务、引入队列中间件或运行无界全量同步。
- 改变排名因子、权重、榜单 API、邮件规则、回测或研究证据合同。
- 在本变更中完成 200/300 交易日的完整研究 replay 深度；发布恢复只优先保证当天和 61 日评分暖机。

## Decisions

### 1. 发布任务复用有界续跑器，而不是扩大旧批次

新增发布就绪协调器，替换 `post_close_etf_adjusted_sync_job` 对旧 `sync_short_research_data` 的调用。协调器先验证同日权威 universe，再读取当天 freshness 和 61 日 warm-up 覆盖；不足时调用一次 `run_bounded_history_sync_slice` 风格的发布就绪切片，结束后重新读取覆盖并决定等待或发布。

发布就绪切片以完整权威 universe 为冻结输入，优先顺序为：

1. 缺目标交易日可决策复权行；
2. 已有当天行但不足 61 个连续可决策交易日；
3. 同一缺口等级内的 tracked/default-display ETF；
4. 持久化轮转后的其他 ETF。

替代方案是把旧 `batch_size` 从 20 提高到 50。该方案仍有逐 ETF sleep、N+1 持久化、粗粒度超时和断点语义不完整的问题，因此拒绝。

### 2. 一次抓取可推进多个事实覆盖，但 lane 状态独立

对于缺当天数据的 ETF，provider 请求范围从目标日向前扩展到覆盖最近 61 个交易所会话及小幅日历缓冲，仍受 `max_rows` 约束。这样一次 raw/adjusted 配对请求通常可以同时补齐当天与暖机历史。

切片完成后分别从持久化的 decision-eligible 行重新计算：

- `daily_freshness`: 目标交易日是否存在合格 `total_return_adjusted` 行；
- `history_depth_61`: 最近 61 个交易所会话是否连续合格。

一个请求可以改善两个覆盖率，但不得因为另一个 lane 的 cursor 前进而直接宣告本 lane 完成。最终发布仍分别检查两个 95% 门槛。

替代方案是先为全 universe 拉一天，再从头为全 universe 拉 61 日。该方案对 1,463 只 ETF 至少重复一轮 provider 请求，因此拒绝。

### 3. 吞吐量由资源预算控制，代码数只是上限

发布就绪切片采用以下冻结上限：

- 一个 worker，数据库租约禁止重叠；
- 初始 `max_codes=10`，生产形状验证通过后最多 `max_codes=20`；
- `page_size<=500`、`max_rows<=5_000`；
- 进程 RSS `<=512 MiB`；
- 45 秒后不接收新 provider 工作；
- 55 秒前完成 commit/rollback 和 checkpoint；
- 60 秒前进程返回；
- 每页最多八条 SQL。

调度在收盘后、门槛不足期间每两分钟尝试一次。租约保证上一个切片未结束时新触发只返回 `overlapping_worker_lease`。发布成功、非交易日、权威 universe 不可用或已到追赶窗口结束时间时不再启动切片。

替代方案是并发 2–4 个 ETF。虽然网络吞吐可能更高，但会增加 provider 限流、AsyncSession 误用、内存峰值和 2 核服务器抖动，因此拒绝。

### 4. Provider 预算下沉并复用切片级连接

当前 bounded runner 的八秒超时包住整个 provider 链，而底层单 provider 默认超时可达二十秒并带重试，可能导致第一个 provider 卡住时永远无法进入下一个 provider。新路径将预算下沉到 provider attempt：

- 切片级复用一个受限 HTTP 连接池；
- 每个 accepted adjusted provider 使用独立的剩余时间预算，单次不超过六秒；
- 同一 ETF 在一个切片内对同一 provider 不做多次重试；
- 连续失败达到阈值后持久化 `retry_after`，后续切片在冷却期跳过该 provider；
- provider 失败后只在剩余预算允许时切换到下一个能提供完整复权 provenance 的 provider；
- raw-only 结果可以记录为 display-only 健康事实，但不得写成 decision-eligible，也不得增加覆盖。

accepted policy 初始包括 TickFlow backward-adjusted、Eastmoney HFQ，以及经完整 provenance 测试通过的 efinance `fqt=2`。Sina 原始日线不属于决策 provider policy。

替代方案是保留固定一秒 sleep 和每 ETF 两次重试。该方案在 20 只切片中仅 sleep 就消耗约 19 秒，且故障源会放大延迟，因此拒绝。

### 5. 断点身份冻结，完整页和断点原子提交

续跑 identity hash 包含：

- 目标交易日和请求日期范围；
- universe hash 及有序 eligible codes；
- ranking/history contract hash；
- provider policy 与 adjustment contract 版本；
- price basis；
- page、row、code、deadline 和 RSS 配置。

复用 `JobRun` 保存 worker lease、最后完整页、最后尝试/完成代码、失败聚合和资源峰值，复用 scope 化 `EtfSyncCursor` 保存轮转位置。每个 code/date 页与 checkpoint 同一事务提交。进程在 commit 前终止时，续跑重新处理该页；唯一键 upsert 保证幂等。identity 不匹配时创建新 continuation，不沿用旧进度。

不持久化 1,463 项 pending queue；每个切片从有索引的 decision-eligible 日线和权威 universe 重新派生缺口，避免动态 universe 下的大型队列迁移。

### 6. JobRun 使用紧凑覆盖摘要

门槛未通过时，任务详情只保存：

- expected/included/excluded 数量与覆盖率；
- exclusion reason 聚合；
- 最多二十个样本代码；
- 本切片 attempted/completed/failed 数量；
- durable checkpoint、provider health、elapsed、RSS、rows/sec 和 remaining estimate。

只有覆盖达到发布门槛后，发布校验在当前事务中构造完整 expected/included sets。完整代码数组不重复写入每个 `JobRun.details_json`，管理 API 也不默认展开它们。

### 7. 渐进启用，而不是直接使用最大吞吐配置

部署后先以十只、五分钟 cadence 观察至少三个真实切片。仅当 P95 elapsed `<30s`、峰值 RSS `<512 MiB`、没有 429/资源告警、没有孤儿 lease 且断点单调前进时，切换到二十只、两分钟 cadence。任一资源或 provider 指标越界时自动退回十只/五分钟，并保留已提交数据和 checkpoint。

本地已有 1,400 ETF × 300 sessions、十只切片约 5.5 秒和约 93 MiB RSS 的生产形状基线；该基线只用于启用前比较，不能代替 VPS 真实观测。

## Risks / Trade-offs

- [Provider 对更密集调用限流] → 使用逐 provider 冷却、无切片内重复重试、两分钟 cadence 和自动降档。
- [一次扩展日期范围返回更多行] → 逐 ETF 串行、5,000 行总上限、500 行分页和 512 MiB RSS gate。
- [当天覆盖先达标但暖机仍不足] → 继续显式等待，不生成 degraded score；下一切片优先 warm-up 缺口。
- [动态 universe 导致 identity 改变] → 结束旧 continuation，使用新 universe hash 创建新身份，不迁移不兼容 cursor。
- [同一请求改善两个 lane 容易误报进度] → lane 完成度只从持久化 decision-eligible 行重新计算，cursor 不作为覆盖事实。
- [管理任务详情缩减后排查信息不足] → 保留 reason 聚合、有限样本和独立只读 readiness 端点，不在 JobRun 中保存重复全量数组。
- [发布生成本身接近切片上限] → 同步切片和生成/发布使用独立有界阶段；只有同步阶段结束且覆盖通过后才启动一次发布校验。

## Migration Plan

1. 冻结旧路径的覆盖、provider provenance、排序优先级和等待状态回归测试。
2. 扩展有界续跑请求/结果以支持发布就绪选择、逐 provider 预算、连接复用、紧凑摘要和不超过二十只的受控配置。
3. 为目标交易日 freshness 与 61 日 warm-up 建立独立覆盖查询，并验证一次抓取不会错误推进另一个 lane。
4. 将 `post_close_etf_adjusted_sync_job` 切到新协调器，保留 95% 发布门槛和旧 API。
5. 新调度器先以 `max_codes=10`、五分钟 cadence 部署，旧同步入口停止被 scheduler 调用但暂不删除。
6. 在真实 VPS 记录三个切片的 elapsed、RSS、provider、checkpoint、覆盖增量和停止原因；满足 gate 后启用二十只、两分钟 cadence。
7. 达到双 95% 后生成并验证一次双榜单快照，确认后自动停止追赶并回归日常模式。
8. 稳定一个交易周后删除仅由本变更产生的旧 scheduler 配置和冗余大型任务明细路径。

回滚只需禁用新 cadence 并把 post-close 调度切回旧有界入口；所有已写入日线仍带真实 provenance 且可保留，checkpoint 和 additive health 状态无需删除。回滚不得恢复原始价格 fallback 或降低发布门槛。

## Open Questions

- VPS 上 TickFlow 与 Eastmoney 的真实成功率和 429 阈值决定是否长期保留二十只/两分钟档位；启用前不假设 provider 能承受理论最大吞吐。
- efinance `fqt=2` 只有在 raw/adjusted 日期严格对齐、版本字段完整并通过 provenance 回归后才能进入 accepted policy；否则保留为非决策诊断源。
