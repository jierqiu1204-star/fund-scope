# 实施与验收记录

## 状态与范围

2026-09-10：业务实现由用户指定的三个 `gpt-5.6-luna / max` 子代理分别负责账户、输入与CLI；主代理独立审查、编写反例、运行测试并核对服务器真实数据。主代理最终独立验收通过；17项实施任务完成。用户恢复腾讯云登录后，服务器已按授权重启，最终代码的追加真实数据复验完成，运行前后应用和数据库均健康。

本轮实现三路线研究比较：ETF龙头突破V2、固定126交易日正动量Top10、原daily_core低换手。仅新增三个业务文件、对应测试与本变更文档；复用原现金/份额账本、V2筛选/生命周期、PIT数据、评分/Stage-B候选和研究artifact。没有新增依赖、数据库表、API、页面或资金引擎，没有更改生产策略、原候选/公式、用户持仓或通知。

工程验收与策略结论分别判定。真实数据当前不足，三账户没有共同合法区间，不能宣布优胜策略；这不以fixture收益替代真实回测。

## 实现落点

| 文件 | 行为 |
|---|---|
| `backend/app/services/strategy_lab/etf_strategy_route_comparison.py` | 固定三路线政策；127根资格；月末稀疏目标；V2确认/退出目标；调用既有基础/压力/零成本账本；同源JSON与中文报告 |
| `backend/app/services/strategy_lab/etf_strategy_route_comparison_inputs.py` | 逐日PIT/clone/历史预检，独立未来估值，V2研究状态重建与续跑，受限研究存储读取 |
| `backend/scripts/run_etf_strategy_route_comparison.py` | `--preflight`、`--prepare-research`、`--compare`；先封存配置，再读取估值和计算收益；固定开发窗口和19:00上海截止 |

历史V2重建复用真实 `screen_dual_universe` 和 `derive_lifecycle`，保留原始preparing观察供后续确认/退出续算。历史回算成果写入独立comparison artifact，`computed_at`记录真实执行时间；不得将今天写出的在线记录伪装成当时已创建。每日状态引用前日封存；缺日、缺观察、源变化或坏hash都不能成为合法续持依据。

V2仅桥接breakout，确认日C/退出日X的目标由原账本在下一个交易日执行；观察日期保留原信号身份，避免重复确认、克隆兄弟或旧生命周期退出误平仓。最多十个10%槽位，名单变化时整组再平衡并收费；名单不变不发目标。中期动量只在声明起始日及真实交易所月末生成目标；daily_core保持原每日候选/缓冲/持有期/替换限制。三者初始现金、共同资格、执行价格和成本一致。共同池先保留原61根基础输入资格，再依据近20日成交额选clone代表，最后应用127根完整窗口门槛。

## 基线身份

基线为 `b2311aa4f5423799da9db0a2c796d62aef3b3f16`。实施前后实际导入逐项比较，原身份均一致，见 [baseline-identities.json](baseline-identities.json) 与 [identity-verification.json](evidence/identity-verification.json)。

| 原契约 | SHA-256 |
|---|---|
| 三候选registry | `5d6841922bbe995f57aa3e2af65ebfe22521bb6bf9844ba1244736ce4f976e14` |
| 20日动量控制 | `9b5de5c4481dcd46a088ca27850a659d98d85852700853c90b68da159eea2d08` |
| V2突破公式 | `acdda06cc2cb043bb26a05c26b55971bc814cefe046e4796178819b5ecf6fc66` |
| V2来源 | `4f08abf21eb7a3c1b0463987b4f3549ef6f39a6c6954f39cc1b44ef97c97686b` |
| 基础成本 | `ea023398e5c5fbda426df403d2f427b80db30d6263324e5cb3817363126f3132` |
| 压力成本 | `6e517d4ba1a62769fb766876781a69a2bccd7aa3fbc8af6dfba63b1565eb25e5` |

基础每边5bp费用+5bp滑点，压力5bp+10bp，毛收益来自原零成本伴随账户。正式五日配对净超额、252个PIT日/40个独立日期/3 folds门槛、三候选晋升与holdout均未改变。研究读写按独立run/phase隔离，不读取其他实验的holdout结果，也不调用旧V1网格脚本或正式晋升流程。

## 主代理验收

独立验收文件：

- `backend/tests/test_etf_route_comparison_acceptance.py`：完整历史资格、低于十只阻断、有效无事件现金、共同区间、现金/份额及费用手算、未来价格不改变过去选择、真实月末/跨月/末日待成交、缺价与截止时间、空/损坏研究存储。
- `backend/tests/test_etf_route_rebuild_acceptance.py`：实际筛选→原生命周期→研究封存→读取→V2目标→原账本，覆盖确认/退出各仅顺延一个交易日、重复运行/中断续跑、真实计算时间、缺日及封存语义破坏。仅替换数据库输入/租约等I/O，不替换筛选、状态或账户函数。
- CLI集成测试直接调用 `_run`，使用十只ETF完整127根PIT OHLCV，验证canonical daily_core/Stage-B/候选、三路线账本、先配置后结果与artifact封存。

最终新功能与领域边界合计 **52 passed in 1.85s**（五个新增模块40项，领域边界12项），见 [final-feature-tests.log](evidence/final-feature-tests.log)。已覆盖初始化父目录缺失/损坏store在租约前拒绝，以及高流动性126根clone代表不得被低流动性127根替换。

主代理运行15个既有相关测试模块：**108 passed，3 failed**。3项失败随后从基线HEAD导出到独立临时目录复跑，全部原样复现，见 [baseline-failures.log](evidence/baseline-failures.log)：

1. `test_etf_pit_replay_input_loader.py::test_research_replay_cannot_write_production_decision_or_notification_state`：原Stage-A调用缺少必填 `required_history_sessions`；新比较入口显式传入自身历史窗口，不调用该旧Stage-A job。
2. `test_etf_action_replay_artifact_store.py::test_replay_outputs_and_checkpoint_commit_in_one_transaction`：旧断言未包含已有 `research: 0`计数。
3. 同文件 `test_sql_failure_rolls_back_outputs_and_checkpoint_together`：同样是旧计数断言。

这3项不是本变更新增回归，未为得到全绿而修改无关生产流程或删除断言。其余范围包括ranking连续账户/候选/Stage-B、V2输入/公式/边界/晋升/锁定案例/生命周期存储、PIT读取/续跑、runtime bounds、artifact与领域边界。

本机无`uv`，主代理使用项目同一环境 `.venv/bin/python -m pytest` 与 `.venv/bin/python -m ruff`。测试子进程去除宿主代理变量，避免宿主SOCKS代理触发缺少可选socksio，未加依赖或修改应用代理配置。每轮测试设120–150秒超时，未跑无关前端构建。

最终backend全量Ruff：**All checks passed**。领域边界：12项通过，包含在52项中。OpenSpec严格校验：**valid**，见 [校验日志](evidence/openspec-validation.log)。所有相关本地测试进程已退出。

## 实际服务器验证与原始证据

通过既有SSH访问服务器，backend/postgres/nginx健康。部署目录不包含Git元数据，原候选、账本、V2、PIT门面与行情5个文件通过SHA-256逐项比对，均与 `b2311aa` 基线一致，见 [部署源校验](evidence/deployed-source-verification.json)。新模块只加载到临时Python进程内，未替换已部署文件。为恢复失去响应的实例，曾按用户授权执行正常重启，恢复证据见下文。行情数据库事务强制只读；直接SQL探查使用8秒超时，CLI将语句超时设为剩余作业预算，整个研究作业限55秒，外层远程进程另有65秒终止保护。

实际共享文件 `/app/data/etf-pit-artifacts/production-pit-research.sqlite3` 已存在（初始180224字节）。不再沿用旧报告“文件缺失”的结论，未复制或改名旧按日期库。比较写入独立 `etf-strategy-route-comparison-20260910-diagnostic` 命名空间，保留共享库其他内容。

冻结开发窗口为 **2026-08-26至2026-09-09**；首轮实际诊断范围为 **2026-09-07至2026-09-09**，每日截止为上海19:00。日期/配置在读取收益前确定。8月已看过的样本保持开发身份，不声称未见验证。

| 日期 | 时点资格数 | 缺少历史clone映射 | 可证明去克隆资格数 | 127根共同合格数 |
|---|---:|---:|---:|---:|
| 2026-09-07 | 1518 | 1518 | 0 | 0 |
| 2026-09-08 | 1518 | 1518 | 0 | 0 |
| 2026-09-09 | 1518 | 1518 | 0 | 0 |

独立只读SQL确认 `etf_tracked_underlying_facts` **整个表0条**，见 [underlying-fact-verification.json](evidence/underlying-fact-verification.json)。ETF V2 manifest为0，transition为0；存在的10份manifest属于A股，不能充当ETF证明。

首次有界探查在逐页重复全池水位查询处超时；改为复用首个PIT页权威元数据+既有批量事实读取/序列校验后，三日探查分别8.95/10.06/10.76秒完成。实际CLI预检25.17秒完成；实际CLI比较也完成并封存5项artifact（预检耗时31.63秒），三条路线均明确不可用，没有收益/回撤/现金优势，也没有三路线排名。

原始证据：

- [实际预检报告](evidence/preflight-20260907-20260909.md)、[完整预检JSON压缩包](evidence/preflight-20260907-20260909.json.gz)、[预检代码身份](evidence/preflight-code-identities.json)。
- [实际三路线报告](evidence/comparison-20260907-20260909.md)、[完整比较JSON压缩包](evidence/comparison-20260907-20260909.json.gz)、[比较代码身份](evidence/comparison-code-identities.json)。
- 初始未压缩比较输出SHA-256：`12a9b8af50cb8d09771d5bf41fb3c6ce94bffeb30d8753b097e9723c3324cc16`；结果hash `ecdeb9511233e2ec9235f0471dc9c6288d31b2216149a0a9b2c1c9cb1278ba77`。

实施中的失败输出另保存在外层工作区 `.tmp/etf-route-apply-20260910/`：首次水位超时、valuation默认页大小不匹配、空共同池仍读取无用估值导致超时；修复后的成功输出独立保存，没有覆盖失败记录。

### 最终代码与追加服务器复验

最后又修正了clone代表在127根门槛前选择、历史封存语义/续跑和准备目录校验，并收紧CLI store作用域。[最终代码身份](evidence/final-code-identities.json)与上面已成功运行时的[代码身份](evidence/comparison-code-identities.json)分别记录，不能将两者混称为同一执行版本。最终代码通过52项本地验收和全量Ruff。

以相同冻结日期、新run `etf-strategy-route-comparison-20260910-acceptance` 追加服务器复验时，SSH连续两次在banner阶段超时；独立TCP banner与HTTP健康检查也超时，进程均已退出，未形成新结果。使用新run是因为输入资格口径修正要求新身份，不是根据收益更换日期或试选策略。

按用户此前提供的入口打开腾讯云控制台后，最初需要重新登录；打开微信确认登录的操作曾被自动审批拒绝，未绕过。用户随后完成登录。2026-09-10约15:31（上海），按此前明确授权提交实例 `lhins-rut7ynl8` 正常重启；约15:39恢复运行。服务器启动时间、三个容器重启及应用/数据库HTTP 200均有实际输出，见 [复验前健康检查](evidence/server-recovery-health-before.json)。

**最终追加复验成功**：使用已经通过验收且SHA-256一致的三个业务文件，在同一冻结日期下执行 `--compare`，run为 `etf-strategy-route-comparison-20260910-acceptance`。预检耗时31.90秒，比较作业完成并写入5项独立研究artifact；三路线仍为`unavailable`，共同区间为空，具体原因与上表一致。没有生成虚假收益或据此认定赢家。

- [最终三路线报告](evidence/final-comparison-20260907-20260909.md)、[最终完整JSON压缩包](evidence/final-comparison-20260907-20260909.json.gz)。
- [最终远程代码身份](evidence/final-remote-code-identities.json)与[本地已验收身份](evidence/final-code-identities.json)完全一致。
- 原始JSON SHA-256：`09454b29ab7fdc0e85509c621c9e5b6a02dedc41431a12530acb5db2d61c5036`；结果hash：`bce6314f7cc43dc9a2baa82eff2085b3c9a547274d520ebe65d686fe28006331`。
- [最终运行与恢复记录](evidence/final-remote-verification.json)；研究phase digest：`74d1268a4a9b5e29141cc8d145a94b82999e3b342a68dfa6924a8fbdc3b106fc`。
- [复验后健康检查](evidence/server-recovery-health-after.json)：应用与数据库均为`ok`、HTTP 200，三个容器运行中，可用内存约2.36GiB；研究进程已正常退出。

此前失败记录和初始诊断成果均保留。重启恢复了服务；本次没有发布新业务代码、删除或覆盖研究结果。工程验收和真实数据复验均完成，剩余的是策略证据缺口。

## 可复跑入口

在包含本次变更代码的backend目录，使用现有项目环境及已配置数据库/研究目录。以下命令适配本机已有的`.venv`；有uv的环境可将解释器前缀替换为`uv run python`：

```bash
.venv/bin/python scripts/run_etf_strategy_route_comparison.py --preflight --start-date 2026-09-07 --end-date 2026-09-09 --max-seconds 55 --json-output /tmp/etf-preflight.json --report-output /tmp/etf-preflight.md
```

仅当预检证明源事实兼容时，显式准备研究状态；同一run/phase重跑复用已校验封存，不能改源/改参数后覆盖旧结果：

```bash
.venv/bin/python scripts/run_etf_strategy_route_comparison.py --prepare-research --start-date 2026-09-07 --end-date 2026-09-09 --run-id etf-strategy-route-comparison-20260910-acceptance --max-seconds 55
.venv/bin/python scripts/run_etf_strategy_route_comparison.py --compare --start-date 2026-09-07 --end-date 2026-09-09 --run-id etf-strategy-route-comparison-20260910-acceptance --max-seconds 55 --json-output /tmp/etf-comparison.json --report-output /tmp/etf-comparison.md
```

CLI输出的`completed`表示比较作业和封存完成；策略可用性须看`comparison.common_status`与每条`routes[].status`，不能把作业完成解释为策略证据合格。`--preflight`不写研究成果；`--prepare-research`只处理合法研究状态；`--compare`写独立配置/目标/账本/报告。异源或不兼容结果要求新run身份，不能覆盖。

## 下一次可评估条件与回滚

需从真实可见时间持续采集权威资格、跟踪指数/克隆映射和合格复权历史，至少十个去克隆代表具有127根完整可用历史；逐日V2筛选和全部活跃信号的状态链完整，估值时各持仓下一交易日有合格价格。今天新采的映射不能回填为9月7日至9日当时已知。127根是历史窗口要求，不机械等同于从今天再等待127天；能否更早评价取决于各历史事实的真实接收时间。

当前CLI严格限定已冻结开发窗口。后续新日期窗口需先预注册新研究身份并更新允许窗口，保持原受保护holdout隔离。未来样本足够后才比较三账户扣费表现、回撤、换手、暴露与集中度，再决定优先优化哪条；短样本不会自动成为正式晋升证据。

回滚只需停止新比较入口并撤回本次新增接线；保留研究artifact和旧manifest供审计。无需数据库迁移，生产排序/持仓/通知没有状态迁移。服务器仅新增独立研究成果，未发布本次代码。
