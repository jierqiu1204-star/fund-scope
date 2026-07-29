## Why

生产环境当前约有 1,463 只 ETF 等待可决策复权数据，而收盘后任务仍按固定 20 只、单日范围和较低频率走旧同步路径；即使当天覆盖逐步补齐，也不能同时满足综合排名所需的 61 个交易日暖机深度，导致 95% 发布门槛可能连续多日无法通过。项目已经具备单工作线程、分页提交、RSS 限制和耐久断点的有界历史续跑器，应将发布恢复统一到该安全执行模型，在不降低数据门槛、不扩大并发的前提下提高串行吞吐量。

## What Changes

- 新增收盘后 ETF 发布就绪协调器，分别度量当天 `total_return_adjusted` 覆盖和 61 个交易日评分暖机覆盖，并在任一门槛不足时执行一个有界续跑切片。
- 将日常发布恢复从旧的固定 20 只逐只同步迁移到现有有界续跑基础设施，冻结交易日、权威 universe hash、排名 contract hash、provider policy 和资源配置，并按完整提交页持久化断点。
- 使用单工作线程和数据库租约禁止重叠；按两分钟节奏续跑，依据时间、行数、RSS 和 provider 健康状态提前停止，达到门槛或完成发布后自动停止追赶。
- 当天缺口同步顺带请求足以覆盖 61 个交易日的有限日期窗口，使同一次 provider 往返可以同时补充当天数据与评分暖机数据，但两个覆盖门槛及其游标状态仍独立核算。
- 复用切片级 HTTP 连接池，并将超时从“整个 provider 链”下沉为逐 provider 预算；持久化冷却和失败轮转，避免一个阻塞源耗尽整个切片。
- 决策同步只接受带完整版本、时间戳、复权口径和 adjustment provenance 的 `total_return_adjusted` 数据；Sina 原始价格以及其他原始或 display-only 数据不得增加发布覆盖。
- 缩减 `JobRun.details_json`，门槛未通过时只记录计数、比例、原因聚合、有限样本和耐久断点；仅在准备发布时构造完整覆盖集合。
- 保持当天决策覆盖和评分覆盖均为 95%，不引入旧快照、估算价、原始价格或降低门槛的 fallback。

## Capabilities

### New Capabilities
- `etf-publish-readiness-sync`: 定义收盘后发布就绪协调、单工作线程有界续跑、双覆盖门槛、耐久断点、资源上限、provider 预算与追赶调度。

### Modified Capabilities
- `short-etf-research`: 将大规模 ETF 日线同步改为面向发布就绪的时间预算续跑，并要求当天覆盖与 61 日评分暖机覆盖分别达到既有门槛后才发布排名。
- `short-etf-research-reliability`: 将旧的固定主备源要求改为具备复权来源证明的 provider policy、逐 provider 超时、持久化冷却和失败轮转，明确原始价格不能成为决策数据。

## Impact

- 后端编排：`app.services.short_research.jobs`、`app.services.workflows` 和 scheduler 的收盘后 ETF 路径。
- 行情同步：`app.services.short_etf.bounded_history_sync` 与 `app.services.short_etf.data` 的请求预算、连接复用、provider policy 和检查点输出。
- 数据库：优先复用 `JobRun`、`EtfSyncCursor` 和现有 ETF health；只有无法表达跨切片 provider 冷却时才增加最小的持久化字段或表。
- API/管理页：任务详情改为紧凑进度、双覆盖率、ETA、停止原因和 provider health，不返回重复的大型全量代码数组。
- 兼容性：不改变现有榜单 API，不降低 95% 发布门槛，不改变排名公式，不允许 Sina/efinance 原始价格参与决策，不启动并发或无界全量同步。
