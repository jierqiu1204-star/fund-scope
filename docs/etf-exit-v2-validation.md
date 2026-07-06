# ETF Exit V2 验证流程

Exit V2 是研究验证层，不是实时交易层。它用于比较“TopN 固定持有、当前退出规则、只暂停加仓 Guard、V2 减仓加再入场”这几套做法，不会发送邮件、不会写真实提醒、不会修改追踪持仓，也不会批准实时参数。

## 推荐服务器顺序

1. 更新 ETF 数据。
   - 收盘后优先运行 `post_close_etf_data`。
   - 如果数据源延迟，可以再运行 `daily_short_research_data` 兜底。
2. 生成 ETF 排名和标签。
   - 收盘后优先运行 `post_close_etf_signals`。
   - 夜间任务 `daily_short_research_signals` 作为兜底。
3. 运行 Exit V2 验证。
   - 后台任务名：`etf_exit_v2_validation`。
   - 该任务内部复用 ETF 策略对照回测，输出 TopN 固定持有、当前退出规则、Guard-only 和 Exit V2 的对照结果。
4. 查看策略证据页。
   - 页面：`/short-term/evidence`。
   - 重点看累计收益、最大回撤、卖飞率、保护率、错误退出、再入场和提醒次数。
5. 再运行策略体检。
   - 后台任务名：`etf_strategy_healthcheck`。
   - 只有同源证据足够时，体检结果才可作为当前策略证据。

## 结果判读

- 如果 Exit V2 收益明显低于 TopN 固定持有，且最大回撤没有显著改善，结论应保持 research-only。
- 如果 Exit V2 最大回撤明显改善，但卖飞率和错误退出过高，也不能直接推广到实时邮件规则。
- 如果样本不足、版本不一致或缺少盘中证据，页面应显示等待证据，不应把旧口径结果当作当前策略证明。

## 边界

- `etf_exit_v2_validation` 只写研究回测结果。
- 不调用通知发送。
- 不创建真实提醒。
- 不修改用户追踪持仓。
- 不批准候选参数进入实时规则。
