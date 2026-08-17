## 1. Contracts and sources

- [x] 1.1 注册创新药 fine-theme 来源、别名和严格 PIT 持久化测试。
- [x] 1.2 登记用户截图来源，保留2026-08-17接收时间并禁止历史倒填。
- [x] 1.3 增加低位补涨公式定义、注册版本、旧 manifest 兼容和 A 股限定。

## 2. Formula and lifecycle

- [x] 2.1 实现120日区间位置、回撤、5对20日量能扩张、连续相对量能、MA5转强和5日突破纯函数。
- [x] 2.2 实现热点/低位/量能/转强四族、等权分数、过热优先级和完整 reason codes。
- [x] 2.3 保留 preparing/confirmed 生命周期，增加5日 watch 和3日 preparing 超时失效。

## 3. Materialization and API

- [x] 3.1 移除两阶段物化“三公式”硬编码并保持有界内存、断点幂等和旧结果兼容。
- [x] 3.2 增加 entry_status 存储投影、API 过滤/字段和稳定 unavailable reasons。
- [x] 3.3 更新前端公式/状态展示，明确 research-only、overextended 和 ETF 隔离。

## 4. Replay and acceptance

- [x] 4.1 复用现有 forward-outcome 实现 A 股1/3/5/10/20日成本后回放。
- [x] 4.2 增加誉衡药业因果案例、边界、PIT、非有限值、物化一致性和无生产副作用测试。
- [x] 4.3 运行每条不超过60秒的相关测试、领域边界、Ruff 和严格 OpenSpec 验收。
- [x] 4.4 在可用数据上执行一次有界影子筛选并报告候选或稳定不可用原因。

## 5. Temporal setup and exemplar diagnostics

- [x] 5.1 将低位和连续量能改为10个交易日内的可审计 setup memory，保存 evidence date/value，并保持严格 cutoff。
- [x] 5.2 将 `launch_signal` 与 entry suitability 分离：ATR 1.5–2.0 保留信号但标记 extended watch，单日2倍放量仅观察，超过2.0或5日涨幅超过25%才过热。
- [x] 5.3 增加风华高科、誉衡药业、利欧股份的因果历史诊断测试，验证合理 setup/launch 时点且禁止未来收益参与筛选。
- [x] 5.4 运行每条不超过60秒的相关测试、领域边界、Ruff 和变更验收，并记录生产只读历史诊断结果。

## 6. Next-morning confirmation

- [x] 6.1 冻结次日上午确认输入、10:40–11:30窗口、7根闭合10分钟K线、1.20倍真实累计量能和ATR安全门槛。
- [x] 6.2 实现最多20只观察候选的有界抓取、严格PIT纯评估和不可变生命周期证据。
- [x] 6.3 将确认结果投影到候选API，保持研究只读、ETF隔离且不产生提醒或持仓副作用。
- [x] 6.4 增加通过、量能不足、过热、迟到、未来K线、身份冲突、幂等、批量上限和002437历史不可用测试。
- [x] 6.5 运行每条不超过60秒的相关测试、内存/续跑、领域边界、Ruff和变更验收。
