## 1. ETF 长历史回填

- [x] 1.1 审查现有 ETF 日线同步入口，确认可复用的 provider、upsert 和失败统计逻辑。
- [x] 1.2 新增 ETF 长历史回填服务参数，支持 `365 / 730 / 1095` 天，默认推荐 `730` 天。
- [x] 1.3 保证重复回填幂等，不产生重复 `etf_code + trade_date` 行。
- [x] 1.4 在回填结果中返回最早日期、最新日期、ETF 数量、行数、失败数量和覆盖缺口。
- [x] 1.5 在 `/api/admin/jobs/{job_name}/run` 和后台任务注册中加入 ETF 长历史回填入口。

## 2. 组合仓位语义改造

- [x] 2.1 找到 ETF 观察组合里“候选不足 4 只即现金等待”的硬规则。
- [x] 2.2 改为“最高可满仓”：1 只最多 30%，2 只最多 60%，3 只最多 90%，4 只以上最多 100%。
- [x] 2.3 保留单只 30% 上限、主题集中度、相关性、流动性和数据可靠性约束。
- [x] 2.4 为剩余现金生成清晰 `cash_reason`，说明是“合格标的不足，剩余资金等待”，不是数据失败。
- [x] 2.5 确保 `portfolio_mode=cash_wait` 只在 0 只 ETF 合格时出现。

## 3. ETF 回测解释增强

- [x] 3.1 回测结果增加请求区间、有效数据区间、预热期、首次信号日、首次交易日。
- [x] 3.2 统计现金等待原因：数据预热、候选不足、风险过滤、无可信价格。
- [x] 3.3 回测支持使用长历史回填后的 `etf_price_history` 数据。
- [x] 3.4 回测结果中区分 ETF 持仓暴露和现金等待比例。

## 4. 前端展示

- [x] 4.1 `/admin/jobs` 增加“回填 365/730/1095 天 ETF 日线”按钮或推荐的 730 天按钮。
- [x] 4.2 `/short-term` 的“ETF 资金配置参考”展示部分仓位和剩余等待现金。
- [x] 4.3 将“现金等待”文案改为“剩余资金等待，不硬凑标的”。
- [x] 4.4 回测展示区显示请求区间、有效数据区间、首次交易日和现金等待原因。
- [x] 4.5 数据历史不足时提示先运行 ETF 长历史回填。

## 5. 测试与校验

- [x] 5.1 后端测试：长历史回填幂等、覆盖统计正确、无效价格不入决策。
- [x] 5.2 后端测试：1/2/3/4 只合格 ETF 的仓位上限分别为 30%/60%/90%/100%。
- [x] 5.3 后端测试：0 只合格 ETF 才返回完整 `cash_wait`。
- [x] 5.4 后端测试：回测正确记录有效数据区间、首次交易日和现金等待原因。
- [x] 5.5 前端类型检查：`corepack pnpm exec tsc --noEmit`。
- [x] 5.6 前端静态构建：`corepack pnpm build:static`。
- [x] 5.7 后端回归：`uv run pytest tests/test_short_research_api.py tests/test_short_research_jobs.py tests/test_scheduler.py`。
- [x] 5.8 后端 lint：`uv run ruff check .`。

## 6. 提交、推送与服务器部署

- [x] 6.1 确认 `.tmp/`、密钥、`.env`、本地缓存没有被 staged。
- [x] 6.2 提交代码，中文简洁 message：`扩展ETF历史与弹性仓位`。
- [x] 6.3 推送 `codex-strategy-lab` 到 Gitee `origin`。
- [x] 6.4 推送 `codex-strategy-lab` 到 GitHub `github`。
- [x] 6.5 SSH 到 `110.42.222.9`，更新 `/srv/fundscope` 到最新提交。
- [x] 6.6 执行 `docker compose -f deploy/docker-compose.ip.yml up -d --build`。
- [x] 6.7 执行数据库迁移：`docker compose -f deploy/docker-compose.ip.yml exec -T backend alembic upgrade head`。
- [x] 6.8 手动运行 ETF `730` 天历史回填。
- [x] 6.9 手动运行 ETF 短线排序、ETF 观察组合、ETF 组合回测。
- [x] 6.10 验证 `/short-term`、`/admin/jobs`、最新 ETF 回测结果。
