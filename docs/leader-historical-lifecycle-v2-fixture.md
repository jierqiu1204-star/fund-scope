# 龙头历史生命周期：封存回归样本

此样本只证明修复后的计算可复现，不是市场回测或策略胜出的证据。输入来自 `backend/tests/test_etf_leader_tactics_historical_backtest.py` 的 `_confirmed_universe()`：15 只合成 ETF、每只 205 根合成日线，信号日期为 2025-06-29；日期连续生成，不代表交易所日历。完整输入 hash 为 `5eb2b7f4f90bbc383c84458a93b22c2ff66605c062885444c745673a27b38024`。封存输入定义、新旧逐笔输出、三个候选汇总和校验 hash 位于 `backend/tests/fixtures/leader_historical_lifecycle_v2.json`。

旧输出在修改实现前读取并封存。旧零成本生命周期 contract 为 `6ee07128b3f28fa553ec438822e2d0ab57810548c753ed10f59735bf9aeb3e5a`；新 contract 为 `28dd4a1bf6c92930289b97645b3ff1da2709734b90226fb5f0c990ab13b5dab4`。旧固定持有期和零成本报告保留原 contract 与指标，在 API 以 `incompatible` 和 `leader_historical_backtest_legacy_contract` 标识旧口径，不改 API 状态枚举。

| 同一笔突破事件 | 旧零成本生命周期 | 新研究生命周期 |
|---|---:|---:|
| 成交基准价 | 16.134 | 16.134 |
| 初始风险的价格锚 | 入场日收盘 16.154 | 实际调整后开盘 16.134 |
| ATR 截止日期 | 入场日 2025-07-01 | 入场前 2025-06-30 |
| ATR20 | 1.3172 | 1.3324 |
| 初始止损 | 13.5196 | 13.4692 |
| 风险单位 | 2.6344 | 2.6648 |
| 每边费用 / 滑点 | 0 / 0 bps | 5 / 5 bps |
| 持有交易栏数 | 1 | 1 |
| 毛收益 | 0.210735% | 0.210735% |
| 净收益 | 0.210735% | 0.010514% |
| 同业净收益 | 0.202799% | 0.002594% |
| 同业净超额 | 0.007936% | 0.007920% |

净收益按 `exit × (1 - slippage) × (1 - fee) / [entry × (1 + slippage) × (1 + fee)] - 1` 计算，同业按同一入场和退出日期执行相同成本。此事件的成本拖累约为 0.200221 个百分点。T 信号低点为 16.2，高于 T+2 的实际开盘，现有纯风险规则明确忽略该低点并使用 ATR 分支；此决定连同输入日期写入事件的研究上下文。改变入场日收盘、最高或最低价不能改变已经冻结的风险和入场成本。

突破候选有 1 条已平仓事件，修复候选为 0 条，修复收益保持 `null`。目前没有已确认的本地封存真实行情可用于本次前后比较，因此没有补造修复样本，也没有生成真实历史表现结论。两个候选都仍需同源真实行情、历史成员及分类快照才能验证。

所有输出保留 current-vintage 成员及同业分类的幸存者/前瞻偏差声明，PIT credit 的三项均为 0。事件序列回撤不等同于受资金约束的组合回撤。本次只修改研究路径，实时邮件的入场语义与零成本保本阈值保持原规则，所以该研究结果不能用作实时邮件策略的同口径证明。

在 `backend` 下运行 `uv run pytest tests/test_etf_leader_tactics_historical_backtest.py tests/test_etf_leader_historical_backtest_script.py tests/test_etf_leader_tactics_evidence_api.py` 可复核封存 hash、前后输出、风险时序、成本和旧身份可读性。新检查点默认使用 `historical-lifecycle-v2.sqlite3`；旧 schema 或旧 contract 的检查点会拒绝写入，并提示使用新路径，不会清空旧事件或替换旧 hash。
