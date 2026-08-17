## Context

当前 ETF 止盈参数来自一个窄夹钳，真实差异主要被上下限吞掉。更严重的是，风险特征使用原始日线，高水位只在写入时保存、读取时忽略，保护线没有单调约束。该修复必须遵守 Market Data → Position Tracking → Risk Alert 的依赖方向，并与综合排名完全隔离。

## Decisions

### 1. 合格复权风险序列

通过 `market_data` 只读门面读取最近最多 120 个 `decision_eligible=true` 且 `research_price_basis=total_return_adjusted` 的日线。阈值特征使用复权收盘；ATR 高低价按同日 `adjusted_close/raw_close` 因子同步换算。数据不足时不得回退到 Sina/efinance 原始价。

### 2. 稳健且冻结的风险单位

当前风险单位取 ATR、实现波动率和绝对收益中位数三项的中位数，并按资产桶做有限约束，不再取最大值。首次得到合格 V2 风险单位后把它作为持仓 episode 的 `entry_risk_unit_pct`；后续阈值使用冻结值，避免波动突然扩大时放松既有保护。

### 3. 单调利润保护

纯规则接收当前收益、当次观察高点、已持久化高点、启动线、回吐幅度和已持久化保护线：

```text
high_water = max(observed_high, persisted_high, current_pnl)
candidate_stop = high_water - trailing_giveback
effective_stop = max(persisted_stop, candidate_stop)
```

只有 `high_water >= profit_start` 才进入 armed。armed 后保护线不得下降；未 armed 时无移动止盈动作。状态写入现有 `exit_state_json`，不推断成交。

### 4. 图表与展示

图表按每个历史点推进同一个纯状态机，形成逐日阶梯式保护线。页面分别展示“止盈启动线”“允许回吐”“当前保护线”和保护状态，并标明复权风险数据是否合格。图线只代表规则轨迹，不代表真实成交。

## Safety and rollback

- 缺少合格复权数据时，ETF 动态止盈不可产生可执行邮件；页面返回稳定 unavailable reason。
- 硬止损、MA5 收盘规则、分阶段动作和综合排名保持独立。
- 规则版本为 `dynamic_etf_threshold_v2`；可回滚到 V1 计算，不删除 V2 状态证据。
