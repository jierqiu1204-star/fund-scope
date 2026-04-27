# Design: add-fundscope-mvp

## Context

FundScope 是一个**新项目**，从零开始构建，无既有代码。目标用户是单一个人用户（CS 大三学生，3w 资金，通过工商银行 App 做场外基金定投）。项目同时承担两个目的：(1) 实际管理用户的定投决策；(2) 作为全栈作品集展示。

关键约束：
- 工商银行 App 无开放 API，所有交易必须手动 —— 本系统仅负责**信息聚合和决策辅助**，不做实盘交易接口
- 单用户部署，目标受众一人，无需注册/多租户/权限系统
- 用户有自有 VPS（国内云 2 核 2G 轻量服务器，~100 元/年），无 GPU
- LLM 通过用户学校提供的 OpenAI 兼容 API 端点调用，零 API 成本
- 场外基金净值**每日更新一次**（17:00-19:00），无实时数据需求

## Goals / Non-Goals

**Goals:**
- 提供一个单体可部署的 Web 应用，统一呈现持仓、估值信号、提醒、新闻四类信息
- 架构足够现代以支撑简历作品集展示（FastAPI + Next.js + Docker + CI）
- 实施复杂度控制在 40-80 小时（3-5 周 × 每周 10-15 小时）的合理范围
- 所有定时任务可观测、可重放、可手动触发（便于开发调试和失败恢复）
- 默认配置开箱可用：新用户无需懂金融即可跑起来

**Non-Goals:**
- 任何形式的价格/收益预测模型（ML / 深度学习 / 时序模型）
- 实盘交易接口（工行无 API，且有监管/安全风险）
- 多用户系统、注册登录、权限管理
- 移动 App 或小程序（响应式 Web 够用）
- 实时行情推送 / WebSocket（数据一天一更，无意义）
- 本地部署 LLM（学校端点就够，节省服务器成本）

## Decisions

### D1. 技术栈：FastAPI + Next.js 单体 VPS 部署（而非 Serverless）

**选择**：单台 VPS 运行 Docker Compose（FastAPI 后端 + Next.js 静态构建 + Postgres + nginx）。

**理由**：
- 用户已决定自租 VPS，单体部署天然简单，无需在 Vercel/Render/Neon 三个服务间协调
- 定时任务用 APScheduler 集成在 FastAPI 进程内，无需额外 worker 或 Lambda；失败重试和手动触发都是本地代码调用
- Serverless 方案的免费层冷启动（Render 15 分钟无请求休眠）对邮件提醒等时效任务有风险
- 单体对个人项目维护成本最低

**Alternatives considered:**
- Vercel + Render + Neon（免费层）：成本 0，但冷启动/免费额度恶心
- Cloudflare Workers + D1：Edge 计算能力超出需求；定时任务模型（Cron Triggers）够用但对 Python 生态不友好
- AWS Lambda + RDS：过度工程，个人项目不划算

### D2. 数据源：AKShare 主 + 天天基金爬虫备

**选择**：基金净值优先走 AKShare (`fund_open_fund_info_em`)；指数 PE/PB 走 AKShare (`stock_zh_index_value_csindex`)；异常时降级到直接 HTTP 抓天天基金公开接口。

**理由**：
- AKShare 是纯 Python 库，无需申请 API Key，覆盖 A 股场外基金 + 中证指数
- 降级方案避免单一数据源中断
- 每天只抓一次，低频调用不会触发反爬

**Alternatives considered:**
- Tushare Pro：功能更强但需要 credits，个人项目不划算
- 直接爬天天基金：AKShare 已经封装，无需重造
- Wind/iFind：收费数据源，个人项目不适用

### D3. 数据模型：SQLAlchemy + Postgres + Alembic

**选择**：关系型数据库（Postgres 16），ORM 层 SQLAlchemy 2.x（async），迁移用 Alembic。

**核心表结构（简化）：**
```
users                   (单用户：一行记录，含 SMTP 配置、watchlist、通知偏好)
funds                   (基金主数据：code, name, type, tracking_index_code)
fund_nav_history        (基金每日净值：fund_code, date, nav, accumulated_nav)
indices                 (指数主数据：code, name, region)
index_valuation_history (指数每日估值：index_code, date, pe, pb, dividend_yield)
portfolios              (组合：单用户默认 1 个)
transactions            (交易流水：portfolio_id, fund_code, action, amount, shares, nav_at_trade, fee, traded_at)
holdings_snapshot       (每日聚合持仓：portfolio_id, date, fund_code, shares, cost, market_value)
news_items              (新闻原文：fund_code, published_at, title, url, raw_content)
news_summaries          (LLM 摘要：news_item_id, summary, model_name, generated_at)
notification_log        (推送审计：type, recipient, sent_at, status, payload_json)
```

**Alternatives considered:**
- SQLite：单用户够用，但 Postgres 对作品集更主流；且 Docker 下 Postgres 部署同样轻量
- MongoDB：无关系需求，用 Postgres 更合理

### D4. 定时任务：APScheduler 进程内嵌

**选择**：APScheduler（`AsyncIOScheduler`）集成在 FastAPI 主进程，任务定义在 `backend/app/services/scheduler.py`。

**任务清单：**
- `daily_fund_nav` — 每日 19:00：抓取 watchlist 内所有基金净值
- `daily_valuation` — 每日 19:15：抓取 watchlist 内所有指数 PE/PB，重算百分位
- `daily_holdings_snapshot` — 每日 19:20：根据最新净值重算持仓快照
- `daily_news_fetch` — 每日 19:30：抓取持仓基金当日新闻 + LLM 摘要
- `monthly_dca_reminder` — 每月 1 号 09:00：根据估值分位计算定投金额，发邮件

**观测性：**
- 每个任务都写入 `notification_log` 或 `job_runs` 表，记录开始/结束时间、结果、错误栈
- FastAPI 暴露 `POST /admin/jobs/{job_name}/run` 手动触发端点，方便调试和失败重放
- 任务失败发邮件到管理员（即用户本人）

**Alternatives considered:**
- 独立 Celery worker + Redis：对单用户过度工程
- 系统 cron + curl 触发 HTTP 端点：部署脆弱，不如进程内嵌
- GitHub Actions cron：不能访问 VPS 上的数据库，还要额外鉴权

### D5. LLM 集成：OpenAI SDK + 学校端点

**选择**：使用 `openai` Python SDK，`base_url` 设置为学校端点，通过 `.env` 注入 `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`MODEL_NAME`。

**新闻摘要 prompt（草案）：**
```
You are a concise financial news summarizer. Given the following news about a
Chinese mutual fund, produce a <=80 Chinese character summary capturing only:
(1) the event type (分红 / 基金经理变动 / 规模变化 / 投资策略变更 / 其他),
(2) the concrete fact. Do NOT speculate on price impact. Do NOT recommend actions.
```

**理由：**
- OpenAI 兼容协议是事实标准，任何兼容端点（智谱/DeepSeek/学校代理）都能换
- 明确限制摘要内容避免 LLM 输出投资建议（合规 + 正确性）

**Alternatives considered:**
- Anthropic SDK：需 Claude API Key，学校端点可能不支持
- Langchain：对单一 prompt 场景太重

### D6. 通知渠道：邮件（SMTP）

**选择**：Python `aiosmtplib` + Jinja2 HTML 模板，QQ 邮箱应用密码作为默认 SMTP 账号。

**理由：** 用户已明确选择邮件。简单、可靠、可归档。

**模板结构：**
```
templates/emails/
├── dca_monthly.html.j2       # 月度定投提醒
├── job_failure.html.j2       # 任务失败告警
└── base.html.j2              # 共享样式
```

### D7. 动态定投金额规则（非预测模型，是规则引擎）

**选择：** 基于 watchlist 中主要宽基指数（沪深300）的 10 年 PE 分位数，按档位调整定投基数。

```
base_amount = budget / planning_months      # 例：30000 / 36 ≈ 833 元
percentile < 20%   → amount = base × 1.5
20% ≤ p < 50%      → amount = base × 1.0
50% ≤ p < 80%      → amount = base × 0.5
percentile ≥ 80%   → amount = 0 (暂停定投，邮件标红提示)
```

用户可在 `/settings` 页面调整基数、档位阈值、参考指数。

**理由：** 简单、可解释、历史回测在 A 股长期定投上胜率不错。明确不引入任何 ML。

### D8. 默认「懒人组合」seed 数据

Alembic 迁移脚本中包含 seed data，初始化数据库时自动插入：

| 基金代码 | 名称 | 类别 | 默认配比 |
|---|---|---|---|
| 007339 | 易方达沪深300ETF联接A | A股宽基 | 40% |
| 001052 | 华夏标普500 | 美股宽基 | 30% |
| 270042 | 广发纳斯达克100 | 美股科技 | 20% |
| 000198 | 天弘余额宝 | 货币基金 | 10% |

用户在 Onboarding 页可一键应用或清空改为自定义。

### D9. 认证策略：nginx basic auth

**选择：** nginx 前置 basic auth 保护整个站点（含 API），不在应用层做登录。

**理由：** 单用户部署，nginx basic auth + HTTPS 足够对抗公网扫描。应用层实现登录就是过度工程。

**缺点：** 未来若要扩展多用户需要重做，但这是明确的 non-goal。

### D10. CI / 部署：GitHub Actions + SSH 到 VPS

**选择：** `.github/workflows/deploy.yml` 在 push 到 `main` 分支时通过 SSH 连接 VPS，执行 `git pull && docker compose up -d --build`。Secrets 存 GitHub Secrets。

**理由：** 单机部署无需 K8s；比 Ansible/Terraform 简单；比手动 ssh 部署可复制。

## Risks / Trade-offs

- **[AKShare 接口稳定性]** → 降级到天天基金直接 HTTP 接口；失败发邮件告警；净值不更新时前端 UI 明确标注「数据截至 YYYY-MM-DD」而不是显示旧数据装作最新
- **[学校 LLM 端点变更或下线]** → LLM 客户端抽象成 Protocol，可快速切换到智谱 GLM-4-Flash（免费）；功能 D 失败不影响 A/B/C
- **[Docker 镜像体积]** → 前端用 `next build && next export` 产出静态文件由 nginx 托管，不在后端容器里跑 Node 运行时，镜像控制在 <500MB
- **[单用户部署单点故障]** → 自动化数据库每日备份到同一 VPS 的 `/var/backups`（可选：上传到用户对象存储）；邮件账号失效时定时任务继续跑但推送失败会在 `/admin/jobs` 看到
- **[开发者精力]** → 分 5 周逐个功能交付；每个功能有明确的可演示 MVP，即使停在 Week 2 也是可用产品（A + B）
- **[过度设计风险]** → 明确 YAGNI 清单（见 Non-Goals）；code review 时严格拒绝预测模型、多用户、实时推送等超范围特性

## Migration Plan

这是全新项目，无既有系统迁移。**首次部署流程：**

1. VPS 准备：Ubuntu 22.04 LTS，安装 Docker + Docker Compose v2，配置域名 A 记录
2. 克隆仓库，复制 `.env.example` → `.env`，填入 SMTP、LLM、Postgres 密码、nginx basic auth 密码
3. `docker compose up -d` 启动所有容器
4. Certbot 容器首次申请证书：`docker compose run --rm certbot certonly --nginx -d <domain>`
5. 访问 `/onboarding`，应用默认组合或录入已有交易
6. 观察第一晚 19:00 定时任务运行日志

**回滚：** Docker 镜像打 tag（git sha），`docker compose up -d --image-tag=<previous-sha>` 回滚；数据库用最近一次备份恢复（每日备份保留 7 天）

## Open Questions

1. **是否需要支持导入天天基金/支付宝的交易历史 CSV？** 降低录入门槛，但格式各家不同，解析复杂度中等。建议 Week 5 再决定是否做，MVP 先手工录入。
2. **是否需要对持仓基金做自动相关性/集中度分析？** 可以帮用户发现「我以为多元化其实全押 A 股科技」这类问题。超出 MVP 范围，P2 功能。
3. **LLM 摘要的中文 token 计费在学校端点如何计量？** 实施时需要实测一周 token 用量，如果超过学校额度需切换备用 LLM。
4. **是否需要配额保护？** 学校端点挂了时 `news-aggregation` 如何降级（目前设计是跳过生成摘要，原文仍入库）——此行为需要在 specs 中明确。
