## Context

FundScope 当前已经有纯规则对象 `AlertDecision`、`PositionActionDecision`、持仓状态 `TrackedPosition.exit_state_json`、提醒记录和提醒审计，但落库流程把几种不同事实连在了一起：风险规则返回一个相对 `target_fraction`，邮件创建时就把建议写入最近仓位动作，跨交易日仅按提醒类型/日期做去重，回测又把持续提醒重复解释为成交。结果是同一持续信号可能把仓位从 100% 连续乘到 50%、25%、12.5%，而不是稳定地保持在一个绝对目标。

该问题横跨 `risk_alerts`、`tracked_positions`、`workflows`、`notifier` 和 Strategy Lab。约束包括：

- 保持项目既定的单向领域依赖，不拆微服务，不引入消息总线或外部告警框架。
- 生产服务器为 2 核 4G；历史回放必须有界、可续跑，不能靠一次无界全量任务完成。
- 金融数据不可信时必须 fail closed；旧邮件、旧建议或 fallback 价格不能被补造成真实执行事实。
- 回测和验证只提供研究证据，不得自动改变综合排名、组合、持仓或生产邮件规则。
- 当前没有券商成交回报，因此“已执行”只能来自用户明确确认或与持仓更新绑定的事实。

## Goals / Non-Goals

**Goals:**

- 将规则评估、告警 episode、仓位动作建议、执行确认和通知投递拆成可关联但互不替代的事实。
- 用绝对目标剩余仓位和数据库幂等约束消除持续信号的连乘减仓与并发重复。
- 明确软提醒、数据等待、硬止损、减仓和退出的语义，并使同一语义同时用于实时流程和回测。
- 在不大规模搜索阈值的前提下，以少量冻结候选、时间顺序样本外和现实成本模型验证改动。
- 为 2 核 4G 环境提供分批、检查点、有限并发和短命令验收方案。

**Non-Goals:**

- 不调整 ETF 综合排名权重、Top-N 发布门槛或 `overextension_atr` 口径。
- 不接入券商自动下单，不把邮件打开/发送成功解释为成交。
- 不引入 Prometheus、Alertmanager、Kafka、Celery 等新的重型运行时。
- 不通过 hyperopt、网格搜索或反复查看最终留出集来寻找更高收益阈值。
- 不用 Sina/efinance 原始价、旧证据或模拟 quote 替代决策级 total-return-adjusted 数据。

## Decisions

### 1. 保持领域单向依赖，以 workflow 负责状态迁移

流程固定为：

```text
market_data -> short_research -> portfolio_allocation
            -> tracked_positions -> risk_alerts
            -> workflows -> notifier
```

- `risk_alerts` 保持纯函数：输入已验证的持仓、价格、阈值和研究证据，输出评估状态、规则候选和绝对目标，不读写数据库、不发送邮件。
- `tracked_positions` 拥有持仓 episode、高水位、当前规则状态和动作事实。
- `workflows` 在一个事务内锁定/校验持仓状态、执行状态迁移、聚合规则候选、幂等落库并创建通知 outbox/提醒记录。
- `notifier` 只渲染和投递已持久化的通知；发送成功、失败或重试均不得创建或推进仓位动作。
- Strategy Lab 复用相同纯规则和状态迁移器，以内存/回测存储替代生产持久化，不反向影响日常链路。

选择该方案是因为它符合现有模块化单体边界，也能用数据库事务解决一致性。备选的独立告警服务或消息总线会增加部署、内存和故障面，不适合当前规模。

### 2. 分离评估状态、规则 alert episode 与聚合 action cycle

每个 `position_episode_id + exposure_version + policy_version + rule_id` 维护轻量 alert 状态机。alert episode id 在规则第一次由 `Normal/Resolved` 进入 `Pending` 或直接进入 `Firing` 时生成，并一直保留到 `Resolved`：

| 当前状态 | 有效事件 | 下一状态 | 计数/episode 规则 |
|---|---|---|---|
| `Normal` | 条件成立 | `Pending`，硬止损可直接 `Firing` | 创建新 alert episode；确认计数从 1 开始 |
| `Normal` | 条件不成立 | `Normal` | 清零确认计数 |
| `Pending` | 条件继续成立且达到确认门槛 | `Firing` | 保留 episode，记录一次 firing transition |
| `Pending` | 条件消失 | `Normal` | 清零计数并关闭未 firing 的 episode |
| `Firing` | 条件继续成立 | `Firing` | 不生成新 episode；只更新可变 evidence |
| `Firing` | 满足恢复入口 | `Recovering` | 保留 episode，开始恢复计数 |
| `Recovering` | 条件重新成立 | `Firing` | 保留原 episode，不重新生成同目标动作 |
| `Recovering` | 达到恢复门槛 | `Resolved` | 关闭 episode；未执行的剩余建议按规则过期 |
| `Resolved` | 条件再次成立 | `Pending`/`Firing` | 创建新的 alert episode |

`DataWaiting`、`NoData` 和 `Error` 是互斥的本次评估数据状态，不属于业务状态机。数据无效时业务状态、确认计数和恢复计数全部冻结；不得推进、恢复、重置或产生动作，也不得用旧价格发送动作型 repeat。已有 action 保持原状态并显示“当前无法复核”，恢复有效数据后从冻结状态继续。

一个持仓从零/关闭变为正持仓时生成新的 `position_episode_id`。在持续正持仓期间，任何净加仓都会增加 `exposure_version`、冻结新的不可变 exposure baseline、重置规则状态并 supersede 旧 exposure 的未完成动作；减仓不增加 exposure version。拆分、合并等公司行为只调整归一化数量并记录 adjustment factor，不视为加仓。完全退出后关闭 position episode，再次建仓生成新 id。

所有能写 `confirmed_shares`、`estimated_shares`、`buy_amount` 或 `status` 的路径（现有 PATCH、份额确认、关闭/恢复、导入、重算和 action execution）必须调用同一个 exposure mutation helper 并携带 `exit_state_version`；禁止路由、脚本或服务直接绕过 CAS、baseline/version、action supersession 和审计。系统补齐首次缺失估算可初始化 baseline；已初始化 baseline 的物化修订必须有来源/审计，不能被误判为用户净加仓或悄悄改写已有 action 目标。

不同规则的 firing transition 先聚合到一个 position-level `action_cycle_id`。当当前 exposure/policy 没有开放 action cycle 且首次出现可执行目标时创建；同一 cycle 内可从较弱目标升级到更严格目标；所有相关规则恢复且未完成建议失效，或 exposure/policy 变化时关闭。这样多个规则可以贡献原因，而不会各自创建重复的 50% 动作。

选择状态迁移而不是固定“冷却 N 天”，因为冷却只能降低邮件频率，无法判断持续条件是否是新风险，也无法安全处理硬止损升级。

### 3. 当前状态保留在持仓 JSON，动作与执行事实独立持久化

为控制迁移规模：

- 在 `TrackedPosition.exit_state_json` 保存 `position_episode_id`、`exposure_version`、不可变 baseline 摘要、`policy_version`、每个规则的当前状态/alert episode id/计数、高水位、单向有效止损线、开放 `action_cycle_id` 和当前有效 action id；新增整数 `exit_state_version` 供 CAS/并发校验。
- 新增持久化 `TrackedPositionActionDecision` 记录，至少包含：owner/position、position episode、exposure version/action cycle、相关 alert episodes、policy/rule versions、所有贡献规则、目标阶段、baseline quantity/weight/adjustment factor、绝对 `target_remaining_fraction`、目标数量/账户权重、输入快照 hash、数据状态、`valid_until`、`proposed|acknowledged|partially_executed|executed|expired|cancelled|superseded` 状态、累计执行数量、`superseded_by_action_id` 及相应时间。
- 每次 owner-confirmed execution 作为不可变 action execution/audit event 记录 idempotency key、执行数量、执行价/来源、执行前后份额、费用、actor、request id 和时间；action 行只保存当前累计投影。
- 在现有提醒/审计记录上增加 alert episode、transition、action decision id、notification item/envelope id 和 policy version 的关联字段；详情证据继续放有 schema/version/大小上限的结构化 JSON，兼容已有 UI。

动作决策唯一键为：

```text
(position_episode_id, exposure_version, policy_version, action_cycle_id, target_stage)
```

`target_stage` 由服务端按版本化绝对目标生成（例如 `remaining_5000bp`、`remaining_0bp`），规则 id 不参与唯一性，只作为原因。一条 exposure/policy 同时只能有一个 current action slot；更严格目标在同一事务内把较弱 action 标为 `superseded` 并接管 slot。仓位行锁/CAS 负责串行化 50% 与 0% 的并发升级，数据库唯一约束负责相同阶段重试。

通知分成 item 与 envelope 两层：

```text
item_key = (alert_episode_id, transition, recipient, channel, repeat_slot)
envelope_key = (owner_id, trade_session, route, severity, channel, sealed_snapshot_hash, digest_revision)
```

多个 item 可关联同一 digest envelope；紧急硬止损使用独立单-item envelope。`repeat_slot` 使用交易所交易日/会话而不是自然 24 小时。价格、ATR、收益率和文案不进入 fingerprint，只进入 evidence；否则数值每次波动都会绕过去重。应用层先判断，数据库唯一约束作为并发最终防线，唯一冲突按已存在结果处理。

备选方案是把所有状态继续塞进 `TrackedPositionAlert` 或 JSON。它无法可靠表达“建议与执行”并提供唯一约束和独立查询，因此只保留易变的当前状态在 JSON，动作事实单独落表。

### 4. 动作采用绝对目标，并在同次评估中合并

每条规则返回相对当前 `exposure_version` 不可变 baseline 的目标剩余仓位，而不是“再卖当前仓位的百分比”。baseline 在新建仓或净加仓时冻结，包含归一化数量、成本/权重、数据来源与公司行为 adjustment factor。基础语义为：

| 规则 | 动作语义 |
|---|---|
| `hard_stop` | 有效数据下目标 0%，每个持仓 episode/动作阶段只建议一次；未处理可重复提醒 |
| `exit_watch` | 仅在同源、有效排名/策略证据确认时，返回版本化策略规定的绝对 0% 或 50% 目标 |
| `trailing_take_profit` | 返回固定的绝对 50% 目标；若版本化策略明确允许且趋势同时破坏，可返回 0% |
| `confirmed_trend_weakening` | 首次有效 episode 返回绝对 50% 目标 |
| `trend_weakening` | `no_add`/保持当前目标，不产生减仓成交 |
| `take_profit_watch` | `hold`/100%，仅观察，不产生动作建议 |
| 数据不可评估 | 无动作 |

同一时点多条规则成立时，workflow 只生成一个主动作，选择最保守的绝对目标并记录全部原因。目标数量为 `baseline_adjusted_quantity * target_remaining_fraction`，但动作严格 sell-only：实际建议目标取 `min(current_adjusted_quantity, calculated_target_quantity)`；当前仓位已经低于目标时只记录 `target_already_satisfied`，绝不反向买回到 50%。

若当前 action cycle 已存在相同或更低的有效目标，新评估只记录状态/通知审计；只有更严格的目标（例如 50% 升级为 0%）才原子 supersede 旧建议并创建新目标阶段。用户在持续 firing 时净加仓会创建新的 exposure version/baseline 并重新评估，旧 key 不会阻止新 exposure 的风险建议。重复执行同一决策得到同一目标，不会发生 50% -> 25% 的连乘。

### 5. 建议、确认、部分执行和完成是不同状态

- `proposed` 表示系统给出建议，不能更新 `latest_executed_position_action`，也不能启动再入场冷却。
- `acknowledged` 仅表示用户已看见/确认建议，仍不能视为成交。
- `partially_executed` 表示已记录部分真实执行，但 resulting quantity 仍高于目标加版本化容差；累计 fill 与剩余差额必须可见。
- `executed` 必须来自所有者显式确认，且 resulting quantity 已达到绝对目标容差，或 0% 目标对应持仓已关闭。
- 更严格目标、净加仓导致的新 exposure version 或 policy retirement 将旧建议原子标为 `superseded`；有效数据确认所有贡献规则恢复时，未执行剩余建议标为 `expired`；用户主动拒绝为 `cancelled`。任何终态都保留原因和已有 execution events，不回写成反向交易。
- 只有第一笔真实 `partially_executed/executed` 减仓或退出 execution fact 才能成为对应再入场冷却的起点；单纯 `proposed/acknowledged`、SMTP 状态或 `Resolved` 不启动冷却。

本变更固定采用窄的 owner-scoped transition API：

```text
POST /api/tracked-positions/{position_id}/actions/{action_id}/transitions
```

请求包含 `transition`、`Idempotency-Key`、`expected_position_state_version`；execute 还必须包含执行时间、数量、价格、价格来源、费用和 resulting shares/close fact。服务端从 action 读取 owner、position、episode、policy 和目标，客户端不得覆盖；action 必须仍属于当前 exposure/policy 且未 expired/superseded。所有数值必须 finite，数量/价格为正、费用和 resulting shares 非负；执行时间不得早于 decision time 或晚于允许的服务器时钟偏差；sell quantity 不得超过执行前份额，且 resulting shares 必须在版本化数量容差内等于 before shares 减累计 fills。0% 目标只有在 resulting shares 位于零容差且持仓同步关闭时才算 executed。

相同 idempotency key 与相同 payload 返回原结果，不同 payload 返回冲突；CAS 不匹配返回冲突并要求刷新。执行事件、action 累计状态、`confirmed_shares/estimated_shares` 或持仓关闭、`exit_state_version` 与审计在同一事务提交。完全相同的已完成重试返回原响应，不重复写 fill。`cancelled` 拒绝当前 action cycle 的同等/较弱目标，alert 继续评估和风险提醒但不重建相同 action；只有更严格升级可创建新 target stage，或所有贡献规则 resolved 后的后续新 cycle 才能再次提出相同目标。

由于当前无券商回报，系统不得自动把 SMTP 接受、用户打开邮件或模拟成交写成真实 execution。回测 execution 使用独立 provenance，不能调用该生产 API。

### 6. 通知是状态变化的只读投影

- 首次进入 `Firing` 可创建 notification item；普通持续状态按用户、交易会话、route 和严重度把多个 item 聚合为一个 envelope/摘要。
- 未处理的硬止损可按交易日重复/升级提醒，但复用原动作 decision id，不创建新动作。
- 高优先级规则抑制同持仓低优先级通知；数据质量失败抑制全部交易建议并产生独立数据状态说明。
- 当前评估数据无效时不得发送引用旧价格的动作型 repeat；历史 action 保持现状，通知只说明“当前无法复核”。
- 静默和重复间隔只影响通知，规则评估与审计继续运行。
- `take_profit_watch` 可进入网页或摘要邮件，但正文必须明确“仅观察、未生成减仓动作”。
- SMTP envelope 使用固定 `Message-ID`；发送器必须先以 CAS 原子 claim `pending/lease_expired` envelope，写入 claim token 与有限 lease，只有持有者可以发送，其他 worker 跳过。状态只记录 `smtp_accepted`、`failed` 或 `unknown`，没有 bounce/回执时不得声称 `delivered`。发送采用 at-least-once 语义：SMTP 接受后、本地状态提交前崩溃或 lease 到期重领仍可能造成外部重复邮件，但重试复用 envelope identity/Message-ID，且任何重复都不得产生新动作。失败记录脱敏错误。

审计事件按 sealed snapshot、状态迁移或 repeat slot 写入，不为每次无变化 poll 插入一行；轮询次数以聚合指标记录。每个事件包含 `event_id/schema_version/from_state/to_state/actor/request_id/occurred_at/recorded_at/causation_id`。API 采用 owner scope、有界分页、字段白名单、recipient/provider/SMTP 错误脱敏和单条 context 大小上限。审计记录在保留期内不可修改；用户删除/隐私策略可以级联删除或匿名化，因此不宣称跨隐私删除的永久留存。

### 7. 实时与回测复用动作契约，通知不驱动成交

日线回测在每个历史日只使用当时已上市且未退市的 point-in-time universe 和当时可得的 total-return-adjusted 数据，禁止用当前 ETF 列表回填历史。T 日收盘生成的 action decision 在基础情景固定使用 T+1 `adjusted_open`（或预登记的固定执行时间戳）成交；不得查看 T+1 high/low/close 后再选择 open。停牌、零成交或无法证明可成交的涨跌停进入 pending/deferred，绝不使用 T 日 close。每笔交易同时记录 raw open、调整因子、归一化 execution price、signal-to-fill 延迟；1/3/5/10 日结果窗口从实际模拟成交日开始。T+1 close 与更高费用/价差/滑点仅是预登记敏感性情景。只有日线数据时，盘中硬止损与 bid/ask/IOPV 仍标记为未验证。

回测事件源是 alert transition/action decision：同一动作幂等键只成交一次；邮件 repeat、发送失败、恢复通知和软提醒均不成交。结果分开报告：

- `action-decision full-execution scenario`：执行所有有效 action decision 后的 1/3/5/10 日和组合净收益、最大回撤、换手、费用。
- `smtp-accepted-email full-execution sensitivity`：只执行 `smtp_accepted` envelope 中 action item 的敏感性结果；不得把 SMTP 接受称为真实送达或用户执行。
- `action benefit`：在同一实际模拟成交时点、同一起始资产下，比较 action policy 与继续持有 counterfactual 的税费后 NAV 差。事件级评价把卖出所得保持现金直到 H，隔离退出动作；组合净收益则按原组合再配置规则处理现金。H 日双方均按市值计价且不假设终端清算费，action 方只扣实际 fill 已发生的成本，避免只给一方虚构 H 日卖出。50% 后升级 0% 的同一 action cycle 作为一条完整 policy path，不作为两个独立准确率样本。
- `protection/opportunity cost`：避免的后续下跌、错失上涨和尾部损失。
- `notification quality`：唯一 episode 提醒数、重复提醒率、无动作提醒率、首次提醒提前量和投递结果。

action accuracy 定义为完成窗口内 `action benefit > 0` 的独立 action cycle 比例，并同时报告平均值、中位数、按 signal trading day 做 cluster/block bootstrap 的置信区间和样本数。“邮件净收益”在 UI/报告中拆成“动作建议完全执行情景”与“仅 SMTP 已接受邮件被执行敏感性”，避免暗示邮件本身创造收益或已真实成交。

旧连乘语义只能通过隔离的 `legacy_current_semantics` 诊断适配器复现，用于解释历史差异；主回测引擎和所有可推广候选仍只接受绝对目标契约，legacy baseline 永远不可成为生产推广候选。

### 8. 固定少量候选，最终留出集只看一次

比较最多包含三个预先登记且版本化的候选：

1. 当前生产语义基线（保留用于解释历史差异）。
2. 仅修复语义：绝对目标、动作幂等、建议/执行分离、`take_profit_watch=hold`。
3. 候选 2 加单向 ATR 移动止盈和趋势恢复迟滞；多头止损线满足 `effective_stop = max(previous_effective_stop, high_watermark - k * adjusted_ATR20)`。

候选 3 在第一次验证前必须冻结 `k`、ATR warm-up、high-water 使用 adjusted high 还是 adjusted close、触发价、恢复迟滞、同日 OHLC 顺序假设和缺失处理。不得用同日 high/low 的未知先后顺序制造可交易路径。

不运行阈值网格或超参优化。本变更的主要选择端点固定为“Top20 合格 ETF、10 个交易日、税费后 action-cycle 平均 benefit”，并以最大回撤不劣于预登记风险容差作为硬门槛；Top3/5/10/20 组合结果以及 1/3/5 日、各 exit reason 仅作次要诊断，不能事后挑选为主结果。语义修复候选 2 按正确性/安全门槛验收，不以旧错误 baseline 收益高低决定是否修复；候选 3 只有在相对候选 2 的 cluster-bootstrap improvement 下界超过预登记最小实际收益、且回撤门槛通过时才可提出推广。数值样本门槛、最小实际收益、风险容差、bootstrap seed/次数/block 长度在第一次 validation/holdout 运行前写入不可变 run contract。

开发期使用时间顺序 walk-forward；任何 10 日 outcome 跨过窗口边界的 action 必须从前一窗口标签 purge。候选与门槛冻结后才运行最终留出集，系统持久化首次 holdout consumption 时间，同一 policy/run contract 不得反复查看后重跑；留出结果不反向调参。比较至少报告总回报、最大回撤、收益回撤比、动作次数、换手、费用、各 exit reason 的 action benefit、错失上涨和样本量。若覆盖、独立 action cycle 数或跨窗口稳定性不足，状态保持 `research_only/sample_insufficient`；任何生产升级必须人工批准并产生新 policy version。

### 9. 回放针对 2 核 4G 采用分批检查点

- 阶段 A 只按 ETF 代码/日期有界分批计算带足够 warm-up 的 point-in-time 调整价特征并落盘；不同候选复用同一特征，不在每个候选中重复拉取/重算。
- 阶段 B 按交易日读取当日完整历史 universe 横截面，统一计算 Top-N/资金竞争，并按时间串行推进现金、持仓、峰值、规则状态、alert episode、action cycle 和 pending fills。代码 chunk 绝不能各自独立排名或独立组合回放。
- 默认单 worker；每次命令只处理一个预估可在 60 秒内完成的显式批次，完整历史由外层续跑调度，禁止单进程内部无界循环或把全市场全历史 DataFrame 常驻内存。
- checkpoint 保存 contract hash、输入数据 snapshot hash、代码/schema 版本、最后完成的代码/交易日、各候选完整组合状态、warm-up 边界和原子完成标志。同 cutoff 数据被修订或任一 hash/version 变化时拒绝续并。
- 相同小样本在不同代码 chunk 大小、从 checkpoint 续跑和一次性运行下，必须产生逐事件一致的信号、Top-N、动作、成交和权益曲线；仅允许预先定义的浮点容差。
- 增加 cutoff truncation invariance：只加载截至 T 的数据与加载全历史时，T 日特征、排名、状态和动作必须一致。

## Risks / Trade-offs

- [迁移后旧建议是否执行不可知] -> 标记为 `legacy_unverified`，不得从旧邮件或 `latest_position_action` 伪造 `executed`；由当前持仓事实重新建立 episode。
- [首次启用时持续信号形成通知风暴] -> 分批初始化；硬止损立即评估，其余规则从 Pending/重新确认开始，先 shadow 后启用通知。
- [JSON 当前状态在并发下丢更新] -> workflow 对持仓行使用事务锁与 `exit_state_version` CAS，current action slot、动作阶段和通知 item/envelope 再由数据库唯一约束兜底。
- [绝对 50% 基准因加仓或公司行为漂移] -> 每个 exposure version 冻结归一化 baseline；净加仓开新版本，拆分只更新 adjustment factor；动作 sell-only 且执行确认记录 resulting shares。
- [较弱建议在升级/恢复后仍被执行] -> 更严格目标原子 supersede；有效恢复使未执行剩余建议 expired；transition API 拒绝非 current exposure/policy 或终态 action。
- [邮件重复允许但用户误认为重复下单] -> 邮件展示同一 action decision id、当前状态和“未自动执行”；回测忽略通知重复。
- [SMTP 接受后落库前崩溃造成重复邮件] -> 固定 Message-ID、声明 at-least-once、重试复用 envelope key；动作幂等完全独立，且不把 SMTP 接受称为送达。
- [T+1 模型仍高估盘中可执行性] -> 同时报次日开盘/收盘、成本压力情景和日线限制，不将日线证据用于证明盘中硬止损。
- [候选 3 仍有参数自由度] -> 参数预登记、候选最多三个、时间顺序验证、最终留出只运行一次且不回调。
- [新增表和索引增加写入] -> 只在 sealed snapshot、状态迁移、动作变化或 repeat slot 写事件，持续轮询只更新有界指标，避免每轮插入 suppressed 记录。
- [按代码分批破坏同日横截面] -> 代码批次只产特征，组合回放始终按完整 point-in-time 横截面和共享现金串行执行，并做 chunk-invariance 测试。

## Migration Plan

1. **Expand**：增加 `exit_state_version`、action decision/执行事件、通知 item/envelope 关联字段和唯一索引；先部署兼容读取，旧表/字段不删除。
2. **Backfill**：为活动持仓按小批次生成 `position_episode_id/exposure_version`。baseline 优先使用确认份额，其次使用可复核估算份额，并用买入日至 cutoff 的决策级调整价初始化高水位；无法确定持仓数量或疑似已清仓的记录标为 `needs_user_confirmation/data_waiting`。旧动作上下文仅标记 execution provenance=`legacy_unverified`，不得推断 executed 或新启冷却。
3. **Shadow**：新状态机只写隔离 shadow evidence，不发送邮件、不改变持仓；比较旧/新评估、重复动作数、current slot、数据等待、baseline 与审计完整性。
4. **Contract reconciliation**：同步修订 `upgrade-etf-exit-with-reentry-sizing` 和 `upgrade-etf-exit-risk-system` 的冲突工件/未完成任务，明确 v2 的 executed-only cooldown、绝对目标和通知/动作分离优先；确定 archive 顺序，防止旧 change 回写旧语义。
5. **Cutover**：通过契约、CAS/并发幂等、完整状态迁移和小样本回放测试后，v2 reader 优先读取新 action/provenance，关闭旧 `latest_position_action` 的相对动作写入和冷却读取；不得双写两套动作。普通通知分批开启，硬止损独立监控。
6. **Validate**：运行冻结候选的两阶段分批 walk-forward；只有调整价覆盖、独立 action cycle、purged window 和样本外门槛满足时才首次消费最终 holdout并生成可评审建议，仍不自动切换生产 policy。
7. **Rollback**：关闭 v2 action/notification generation并回到安全的“仅展示/不生成交易动作”模式，保留新增表和审计数据；不得回滚到会重复连乘减仓或把邮件当执行的旧路径。数据库删除迁移另行评审，不在紧急回滚中执行。

## Open Questions

- 在第一次 validation/holdout 运行前，需要根据可用真实历史预登记最小独立 action cycle 数、窗口长度、最大回撤容差和 block-bootstrap 参数；这些值必须写入不可变 run contract，看到结果后不得修改。
- 用户部分执行后的 realized P&L 展示是否在本变更内补齐，取决于当前持仓成本字段能否无歧义表达剩余成本；无论 UI 是否展示 realized P&L，执行事件、remaining shares 与 action 状态的正确性是本变更必做项。
