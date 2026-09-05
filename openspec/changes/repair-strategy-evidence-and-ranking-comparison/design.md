## Context

动机见 `proposal.md`。实施基线为 `worktree-0811`，HEAD `b73263f`；不在根目录旧副本或已归档实验上继续开发。

本次复核发现的实际断点：

| 断点 | 现有位置 | 最小处理 |
| --- | --- | --- |
| 标签验证仅处理最新一期 | `short_research/service.py` 的 `run_etf_signal_validation`、`review_etf_label_outcomes` | 改处理历史到期 cohort，仍用已有 outcome/run/item 表 |
| production 来源固定旧 v3 | `etf_validation_session_planner.py`、`etf_ranking_validation.py` | 显式按真实 source contract 读取与校验 |
| 候选每天重置状态且没有门控事实 | `workflows/etf_point_in_time_capture.py` 的 candidates handler | 从已封存 artifact 接续状态，读取时点内已有事实 |
| 收益只有信号日和空价格 | 同文件 forward_outcomes handler | 接入真实后续交易日、复权价和成本来源 |
| 验证和 evidence 为固定 pending | 同文件 ranking_validation/factor_evidence handlers | 调用已有计算器，按实际依赖返回可用或不足 |
| walk-forward 测试引用不存在模块 | `test_etf_ranking_walk_forward.py` | 合并到现有统计实现与测试，保留必要语义断言 |
| 龙头风险锚错价且未扣成本 | `etf_leader_tactics_historical_backtest.py` 的生成与退出重放路径 | 新研究版本：实际 entry_price、此前 ATR、非零成本 |

2026-09-05 的线上汇总用于说明缺口，不作为固定测试预期：51 次 forward validation 失败、15 份当前研究榜发布快照、最新 11 个 ETF 龙头 PIT 观察日。9 月 4 日无正式 ETF 榜可以解释当日失败，不能据此认定全部历史失败同因。

## Goals / Non-Goals

**Goals:**

- 让已有生产快照和历史样本真正进入候选 → 收益 → 配对验证 → 证据的闭环。
- 在不改当前评分和正式三候选身份的前提下，提供可重复的成本、换手和超额比较。
- 修复已发现的研究执行问题，明确“软件已接通”和“策略已被证明”是两个验收结果。

**Non-Goals:**

- 不增加服务、消息队列、调度框架、插件体系、数据库表、外部依赖或 API 路由。
- 不补造历史 PIT 接收时间，不重建七策略引擎，不扩大因子/形态/参数搜索。
- 不修改实时仓位、风控参数或邮件，不宣称修正后的研究回测等同当前实时退出规则。
- 不把本次实现的完成条件设成“收集够一年”或“收益必须提高”。

## Decisions

### 1. 在已有领域模块补实现，协调留在 workflows

信号/价格读取仍由原服务负责；候选和研究统计仍在 `strategy_lab`；跨日期续跑、artifact 读取和持久化协调留在现有 workflow。`notifier` 不参与研究判断，纯证据契约模块不读数据库。

直接复用 `ReplayArtifactStore`、`EtfFactorExperimentCheckpoint`、`EtfFactorExperimentEvidence` 及 `EtfLabelOutcome` / `EtfSignalValidationRun/Item`。新增信息放现有 JSON 和既有契约字段，避免新表、新通用基类或另一条 pipeline。归档任务未完成处由本提案承接，不改写历史 tasks 以伪装完成。

### 2. 信号截止时间与收益截止时间分开

信号只能使用 T 时可见的成员、标签、评分与价格。后续收益使用在各执行/结果截止时间前可见的真实未来价格，不能要求未来价格在 T 已存在。

正式发布来源以原始 persisted contract、score field、rule、publication state 和 price basis 校验。当前 research surface 与旧 v3、research replay 各自分组，适配来源选择和取分全过程；不全局替换旧常量、不将旧结果盖上新 hash。缺失且无法从封存来源验证的身份，保持 legacy。

`EtfLabelOutcome` 的真实唯一键是 `(signal_item_id, horizon_days)`，本次不改键：该表仅成熟原契约兼容的 pending/缺价记录，已经完成的值保持冻结。相同信号的新执行契约或已完成结果的新输入 revision 写现有 artifact/evidence，并由新 validation summary 显式引用，不能靠新建 validation run 覆盖旧 outcome 行。连续日期的兼容组可汇总，但保留各日期与来源。尚未到期为 pending；已经到期但缺价格为 missing-data exclusion，后续合法事实到达仍可重试。

没有今日榜时，先处理已有历史到期样本，再独立报告今日发布状态。采用已有会话规划器和续跑机制，按最老到期单位每次处理一页，跳过尚未到期的单位；不把来源简单改成“昨天”。

### 3. 保存信号不可变性，同时允许结果成熟

原 source、ranking、candidate selection 和候选跨日 state 是不可变研究输入。后续价格到达时，用现有 artifact/checkpoint 身份增加 outcome cutoff 和 input hash 对应的结果 revision，不覆盖原信号 artifact，也不把正常成熟误判为原 manifest 被篡改。

最终 evidence 引用明确的来源与结果 revision。相同 revision 重试必须复用；真正改变评分、候选、成本或执行规则必须新 run。旧 placeholder-complete checkpoint 用新的兼容工作 revision 恢复缺失阶段，不删除历史记录、不将旧 done 当作有统计结果。

保留现有单 worker、lease、5–20 只分页和每次最多 55 秒边界；超时提交完整页并释放租约。已有 capture 调度保持优先，验证积压和新采集分开处理，避免一个永久缺数的来源阻塞全队列。

### 4. 三候选必须沿交易日接续，而不是每天重新开始

复用 `FROZEN_RANKING_CANDIDATES` 与 `evaluate_ranking_candidates`：

- `daily_core_top10`：原始研究榜前十。
- `daily_core_top10_hysteresis`：保留至第十五名、至少持有三天、每天最多换一只。
- `daily_core_top10_hysteresis_regime`：同样的缓冲选择，再应用冻结市场/流动性门槛。

现有 `production-pit:{source_context_hash}` 是每份来源单独一个 run，不能按相同日 run_key 找前一天。使用现有 JSON/hash 定义稳定 state-chain identity，包含 source kind、score/candidate/execution contract、固定研究起始日及初始化政策；每天的 artifact 明确引用本日 source run、前驱交易日与 state hash，跨日读取只允许同一链。原来的每日 source run 身份保持不变，不加状态链表。

从这条链的前一已封存交易日 artifact 恢复 previous_states。首次状态必须在指定起点初始化；中间缺状态不能悄悄重置，应恢复缺页或标不可用。门控事实只能来自当时已保存且截止时间合格的数据；缺事实只排除相应候选，不代入中性市场。

读取完整合格后续 session 和 adjusted close 后，继续用 `calculate_ranking_forward_outcomes` 生成固定持有期事件诊断；该计算器目前每个事件固定扣往返费，不足以代表低换手策略的正式净收益。正式五日配对样本必须使用下节连续账户的实际成本路径，不把该事件计算器已经扣费的返回值再扣一次。

### 5. 正式主检验固定，纯动量只提供额外诊断

正式基线仍是原冻结 research Top10，主指标仍为相同日期和 common-support universe 上 Top10 五交易日净收益的配对差，但本次明确为连续策略的五日表现并冻结新的 ranking execution/endpoint 子契约与 hash。原固定持有期事件语义不混入新主样本；通用因子实验的旧 manifest、公式与统计接口不全局修改。纯动量不能写入正式 baseline 字段覆盖原语义。

额外控制固定为 `20 日复权收益 > 0`、降序、代码打破并列、最多十只、每只 10% 目标权重、剩余现金。复用相同合格/去克隆候选池和执行计算，将其独立 hash 与结果写 evidence 的 diagnostic 部分。不给它注册第四候选，不挑最优 Top N，不提供参数网格；旧 Top4 结果仅保留历史说明。

成本维持现有每边手续费 5bp + 滑点 5bp；固定压力情景只将滑点增至每边 10bp，不参与寻优或晋升。现有 factual spread/liquidity 成本来源继续单列；冻结费用估计与事实执行成本不能混标。

### 6. 事件收益与连续资金曲线分开，避免再平衡漏费

正式五日 paired endpoint 与连续资金诊断复用同一套最小现金/份额记账，不能把各日重叠事件收益连乘成账户收益。已有 paired difference、bootstrap 和门槛计算器继续复用，只修正其净收益输入来源。

连续资金诊断使用最小现金/份额记账：先用当日合格价格更新实际资产价值，再依据前一信号日已知目标于 T+1 合格复权收盘执行，仅对实际买卖金额扣费；买入受可用现金约束。相同等权目标不代表没有成交，要先反映价格变化后的权重漂移；未实际交易的持仓不重复扣往返费。

主样本窗口精确定义为 **T+1 收盘调仓前净值 → T+6 收盘调仓前净值**，覆盖五个完整价格区间。窗口计入 T+1 至 T+5 收盘的实际费用；T+6 调仓费用归后续区间。窗口开始承接之前的现金/份额，窗口末只估值，不为每个统计窗口重建仓或虚构清仓。每笔交易在账本中只扣一次，提取窗口收益时不再调用固定往返扣费公式。

毛收益用相同目标规则的零成本伴随账户计算，净收益用含成本账户计算；不得把累计费用比例简单加回净收益。将同窗 candidate/baseline 毛净收益填入现有配对样本格式，再执行已有统计。新 execution/endpoint 身份写现有 manifest/report 字段，旧结果保持独立。

优先复用 `short_research/backtest.py` 已有记账原语；如其整个入口绑定旧标签、开盘执行或实时规则，则只在现有 ranking outcome 模块加入小型纯资金累加函数，不接入旧入口、不创建新账户/订单体系。输出写现有 evidence JSON，曲线按现有压缩/采样方式保存，明细不重复灌表。

资金诊断记录累计净收益、资金最大回撤、换手（实际成交额/初始研究资金）、费用、调仓次数/订单次数的明确单位、现金比例、覆盖及缺数原因。必需的持仓估值或执行价缺失时该区间不可用，不用旧价、不自动假装清仓；跨候选比较只用同一预声明的完整区间，不能删除亏损/缺数日再连线。

### 7. 使用现有统计控制，完成真正的接线

将成熟来源汇集为配对样本，接入 `etf_ranking_validation` 与 `etf_factor_validation` 的已有 endpoint、fold、bootstrap、Holm 和门槛函数；再调用已有 factor evidence builder，替换固定 pending 的 handler。

`etf_factor_validation.expanding_walk_forward_folds` 与孤儿 ranking 测试的 embargo 位置不同，不能简单改 import。沿既有冻结 split/真实 outcome 区间验证训练早于测试、跨界标签 purge 和 10 session embargo；对尚未实现的边界语义做最小适配并记录新的 split hash。将有用的不泄漏/完整fold/不可重调断言迁入现有测试后删除孤儿测试，不为文件名补建平行模块。

一次留出复用 `HoldoutConsumption`、`consume_holdout_once` 与已有冻结授权，收据写现有 checkpoint/artifact，在持锁且校验成功后、读取留出结果前原子占用；重启/并发/重试均不能二次消费。结果重读返回已保存证据，不重新访问留出。数据不足时不消费留出。

保留 252 PIT session、40 非重叠主日期、3 folds、95% 决策数据覆盖、90% 61日暖机覆盖和所有现有置信/风险门槛。测试可用小型 fixture，生产常量不得降低。

不为跑通 ranking 指标强建所有去混杂因子、action-policy 或通知证据：已有数据的分支可显示诊断结果，缺失分支继续准确标注。完成 handler 不等于晋升；不满足任一必要晋升门槛仍 insufficient/unconfirmed。

### 8. 龙头只修研究生命周期，保持实时语义冻结

在历史信号生成及 `replay_historical_exit_policy` 两个路径复用同一个研究风险初始化步骤：T+2 的真实 adjusted open 为 entry anchor，ATR 用切片到 entry_index 之前的已完成 bar，信号 low 固定为 T。复用 `initial_leader_risk` 与退出纯规则，但传入真实研究 entry_price；不要因参数旧名 `entry_close` 而全局重命名或改 live 调用。

冻结一个新的 lifecycle execution/risk/cost contract，写明入场、确认、退出规则、风险输入日期及研究费用。手续费与滑点复用研究常量，不能修改 live 的共享成本/风控常量。同行收益使用同入退时间和披露的同费用约定，毛、净、超额分别计算。

固定持有期 next-close 试验、历史零成本生命周期、修正生命周期三种身份独立；旧结果不覆盖。当前成员/行业映射的幸存者限制保持，promotion credit 仍为零；事件序列回撤仍不得显示为资金回撤。

### 9. 复用证据页面，不做新产品面板

通过现有 evidence/validation API 和面板展示：来源与版本、最新信号日、结果截止日、pending/成熟/缺数数量、独立日期、成本、三候选诊断和具体不足原因。新增元数据只进已有 JSON；保持路由、鉴权和现有类型兼容。

页面能同时说明“今日榜等待”和“历史结果已推进”，但不显示内部 handler 类名、cursor 或假完成进度。旧结果显示旧口径；不加“自动推荐最优策略”按钮。

## Risks / Trade-offs

- **研究数据仍然很短** → 可交付正确的采集、成熟与诊断能力，不能承诺当前就获得正式赢家或通过留出。
- **历史来源缺契约/时点事实** → 仅恢复可验证来源，其余 legacy/unavailable；不通过补 hash 或回灌当前分类提高样本数。
- **不可变 artifact 与可成熟 outcome 冲突** → 结果 cutoff/input revision 独立命名，原信号和候选状态保留不动；回归测试覆盖成熟与重复执行。
- **候选状态缺一日可能污染以后日期** → 从已验证的最早缺页恢复，同一契约按时间顺序处理；不清空持仓状态继续算。
- **统计工具语义与旧测试不一致** → 以冻结契约和实际持有区间验收，不以测试变绿为目标简化 purge/embargo。
- **现有研究流程范围大** → 只补排名与本次龙头必需分支；无关因子/策略/通知分支保持明确不足，不借机重构。
- **修正风险后与线上退出不再同源** → 明示独立研究版本；实时修复另提变更，不自动改既有仓位。
- **服务器资源有限** → 保留已有55秒续跑上限、单worker和分页，验证命令设置总超时；没有必要的全量重算不启动。

## Migration Plan

1. 本次提案只落文档；实施先补能复现断点的最小测试，再按 P0/P1/P2 顺序接通，提交可独立验收的小批改动。
2. 不执行数据库迁移、不恢复已删除 API、不覆盖旧证据。更新后的研究逻辑使用新 run/code/execution/split 身份，冻结评分与三候选身份保持原值。
3. 在本地合成的多日期链路完成验收，再用可验证的封存研究输入做一次小范围诊断，保存来源、旧新指标、缺数原因和不可证明项；没有足够数据如实记录，不把任务无限挂起等待市场数据。
4. 准备线上运行说明和前后核对 SQL：生产排名、持仓、风险及通知应无变更；验证成熟数、失败原因和费用可见。服务器部署或生产批量续跑不属于本提案生成，也不作为本次计划完成动作。
5. 如后续获准上线后需回滚，恢复前一代码版本并停止新研究 continuation，保留新旧研究证据供审计；无 DDL、无实时阈值迁移，因此无需回滚业务持仓数据。
