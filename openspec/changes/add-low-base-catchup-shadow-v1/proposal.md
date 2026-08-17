## Why

现有龙头战法能够研究突破、筑底启动和前龙修复，但没有把用户提供的“超跌、热点、底部连续放量、初始转强”公开条件冻结为独立假设。创新药也尚未注册为可追溯细分主题，导致誉衡药业等案例只能退回宽行业，无法形成可审计的主题证据。

## What Changes

- 新增仅适用于 A 股研究的 `low_base_catchup_proxy_v1`，保持现有三个公式、ETF 综合排名、生产推荐和提醒不变。
- 注册可审计的创新药公开概念来源并按接收时间保存不可变 PIT 成员事实；历史缺失不得以当前成员倒填。
- 冻结120日低位、回撤、连续放量、MA5转强、近5日突破和过热上限的精确数学定义；低位与量能证据可在有界 setup 窗口内先发生，突破日只负责触发起飞信号。
- 保留 `turning_watch -> preparing -> confirmed -> invalidated` 生命周期，并新增独立 `entry_status` 表达观察、可行动、过热和失效。
- 复用现有有界物化、成本模型和 forward-outcome 引擎，增加 A 股1/3/5/10/20日影子回放。
- 将用户截图登记为 `user_supplied_capture`，只使用2026-08-17接收时间，不倒填发布日期或历史可见性。
- 增加风华高科、誉衡药业和利欧股份的历史诊断案例，区分“出现起飞信号”与“信号日仍适合行动”，不得用三只已知结果优化正式参数。
- 对单日强放量观察候选增加次交易日上午的严格 PIT 二次确认：只读取有界候选池的已闭合10分钟行情，满足第二日强量能且价格仍在安全 ATR 区间时才升级为可行动；不以收盘数据倒填盘中买点。

## Capabilities

### Modified Capabilities

- `a-share-point-in-time-research-data`: 注册创新药细分主题并保存严格接收时间证据。
- `dual-universe-leader-tactics-screen`: 增加独立低位补涨影子公式和 entry status。
- `dual-universe-leader-tactics-evidence`: 增加来源捕获、公式事实、状态距离和回放证据。
- `dual-universe-leader-tactics-validation`: 增加 A 股多期限成本后 forward outcome。
- `etf-label-validation-dashboard`: 在龙头战法研究面板中独立展示该影子公式。

## Impact

- 后端：公式注册、纯计算、PIT 主题适配、物化完整性、存储/API schema、A 股回放、次日上午有界盘中确认。
- 前端：公式筛选、entry status、原因与事实展示。
- 数据：新增创新药主题事实，不删除或改写旧 manifest。
- 明确不影响 ETF 综合排名、生产推荐、组合、持仓、提醒和通知。
