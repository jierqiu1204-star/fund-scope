## Context

V2 当前冻结三个候选，并在两阶段低内存物化中按“资产数 × 3”校验终态数。候选公式通过后进入 `preparing`，随后收盘突破信号日高点且守住复权 MA5 才进入 `confirmed`，跌破 MA5 则失效。创新药尚未注册为 fine theme；用户截图也无法由当前要求已知发布日期和 URL 的文章模型表达。

## Decisions

### 1. Append one isolated A-share formula

新增 `low_base_catchup_proxy_v1` 并升级公式注册版本。旧公式定义不变，旧 manifest 继续只读兼容。ETF manifest 和观察集合继续只包含原有三个公式，避免改变 ETF 候选集合或综合排名链路。

### 2. Freeze exact PIT formulas with bounded setup memory

对信号日 T：

- 输入至少需要 `120+10-1=129` 个合格交易日，保证 setup 窗口中每个候选证据日都有完整120日历史；不足时失败关闭。
- 对每个可决策交易日 d 计算 `range_position_120[d]`、`drawdown_120[d]`、`volume_expansion_5v20[d]` 和 repeated relative-volume facts。
- 在 `S=[T-9:T]` 中必须存在可审计的低位证据：`min(drawdown_120[S]) <= -0.25` 且 `min(range_position_120[S]) <= 0.40`；保存各自 evidence date/value，零区间失败关闭。
- 在同一 `S` 中必须存在某个交易日 d 满足 `volume_expansion_5v20[d] >= 1.30`，且该日最近5个交易日中至少2日满足 `V_i/mean(V[i-20:i-1]) >= 1.20`；保存 volume evidence date/value。
- 单日相对20日均量达到2.00倍且同日起飞触发成立、但尚未满足上述连续量能门槛时，只进入 `turning_watch` 并记录 `single_day_volume_watch` 与 `entry_status=watch`；它不是确认，也不能产生可行动候选。
- `C_T>MA5_T`、`MA5_T>MA5_T-1`、`C_T>max(H[T-5:T-1])` 且 `C_T>C_T-1`。
- `return_5<=0.25`。`abs(C_T-MA20_T)/ATR20_T<=1.50` 时可行动；位于 `(1.50,2.00]` 时仍记录 `launch_signal=true`，但 `entry_status=watch`、`extension_band=extended_watch`；超过2.00时 `entry_status=overextended`。
- `hot_score>=2/3`、可比股票不少于5只；公式不再要求同主题20%资产同时通过完整低位条件。

所有价格均为可决策 total-return-adjusted 数据；量能仅使用同一信号截止时间前的真实日线。缺失、非有限值、禁止提供方或非 PIT 主题失败关闭。

### 3. Preserve lifecycle; separate setup, launch and entry suitability

- `turning_watch`: PIT 输入有效，热点、低位和量能三族在最近10个交易日的 setup 窗口内已通过，仅缺初始转强；观察期内收盘突破前5日高点且守住复权 MA5 后进入 `preparing`。低位和量能事实必须来自 cutoff 前真实交易日，不能由后续价格反推。
- `preparing`: 完整起飞触发首次通过并记录 `launch_signal=true`；ATR 延伸不超过1.50时 `entry_status=actionable`，位于 `(1.50,2.00]` 时保留信号但 `entry_status=watch`。若只有单日2倍强放量而连续量能尚未确认，也仅为 `watch`。
- `confirmed`: `preparing` 后的另一个合格交易日收盘突破 preparing 高点并守住 MA5。
- `invalidated`: 后续收盘跌破 MA5，或 `turning_watch` 超过5个交易日，或 `preparing` 超过3个交易日未确认。
- 任一信号日若5日涨幅超过25%或 ATR 延伸超过2.0，优先返回 `entry_status=overextended`，不作为可行动候选。

`entry_status` 不替代 lifecycle state，也不接入交易或通知。

### 4. Add auditable innovation-drug facts

注册提供方精确标签“创新药”和标准键 `innovation_drug`。每次抓取保存原标签、标准键、有效日、接收时间、来源、成员哈希。仅接收时间不晚于决策 cutoff 的事实可参与计算。当前成员事实不能回填此前交易日。

### 5. Store user evidence without inventing metadata

用户截图使用内容哈希、`source_kind=user_supplied_capture`、`received_at=2026-08-17` 和未知发布日期状态登记。该证据只解释公开条件，不能证明原作者未公开的起飞公式，也不能成为2026-08-17之前的 PIT 决策输入。

### 6. Reuse bounded materialization and replay

两阶段物化继续每批5–20只、单工作器、单次不超过55秒。终态计数改为按适用公式集合计算，禁止继续写死三个。A 股回放复用现有 adjusted-close forward outcome，买入为信号后首个合格交易日复权收盘，报告1/3/5/10/20日并应用现有双边费用和滑点。

### 7. Confirm a single-day-volume watch on the next morning

- 只处理前一合格交易日已物化、`single_day_volume_watch=true`、`entry_status=watch` 的 A 股低位补涨观察；每批最多20只，不扫描全市场。
- 10:42执行一次有界定向抓取并立即只读评估，不预设未来截止时间；决策窗口固定为上海时间10:40–11:30，至少需要从09:30开始连续7根已闭合10分钟K线。
- 每根盘中事实必须满足 `bar_end<=decision_at`、`received_at<=decision_at`、`decision_eligible=true`，且整组使用同一非空复权标准化身份。迟到、缺口、重复冲突和原始价格替代一律失败关闭。
- 第二日强量能采用保守定义：截至决策时的真实累计成交量，已经达到信号日前20个合格交易日平均全天成交量的1.20倍；不做全天量能外推。
- 实时复权价必须高于信号日复权收盘、守住信号日复权MA5，且 `abs(current_adjusted_price-signal_adjusted_MA20)/signal_adjusted_ATR20<=1.50`。
- 全部门槛通过才追加不可变生命周期证据，将候选升级为 `confirmed/actionable`；否则继续 `watch`，不得修改原观察、发送提醒、创建持仓或影响ETF综合排名。

## Risks / Trade-offs

- 历史创新药成员证据不足时，价格量能案例可以回放，但完整主题门槛必须显示不可用。
- 第四公式使 A 股观察行约增加三分之一；按现有分组有界流程处理，不扩大同时驻留内存。
- 参数来自公开规则工程化而非收益优化，先保持 shadow，不能因誉衡药业已知结果调整。
- 风华高科、誉衡药业和利欧股份只用于因果诊断：测试应证明时序结构能够表达已知 setup/launch 差异，但不得据此选择收益最优阈值或宣称时间外有效性。
- 生产库没有誉衡药业2026-08-10上午的真实接收时点行情，因此新路径只能证明未来可运行，不能把历史日线拆成虚构的上午确认。

## Rollback

关闭龙头战法物化/API 开关即可停止新结果；旧 manifest、主题事实和来源捕获保持不可变。回滚代码后旧三个公式仍可读取，新增公式 manifest 不作为旧版本兼容输入。
