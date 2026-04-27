# Proposal: add-fundscope-mvp

## Why

用户是 CS 大三学生，手握 3w 人民币个人资金，希望通过工商银行 App 做场外基金定投，但缺乏金融背景且工行 App 无开放 API（交易必须手动）。市面上的基金 App 要么数据散落（支付宝/天天基金/券商各看一部分），要么强推主动管理导向用户频繁操作。需要一个**不预测、只聚合**的个人定投辅助工具，把纪律性定投决策所需的信息（持仓、估值、提醒、新闻）统一呈现，所有买卖由用户在工行 App 手动执行。项目同时作为全栈作品集（Next.js + FastAPI + VPS 部署）。

## What Changes

- 新增一个独立 Web 应用 FundScope（单用户，部署在用户自有 VPS），包含四个核心功能模块：
  - 定投记账 + 持仓/收益可视化
  - 指数估值分位数监控（PE/PB 历史百分位，作为定投金额调整信号）
  - 定投执行提醒（每月邮件推送，根据估值动态调整金额）
  - 基金新闻聚合 + LLM 摘要（通过用户学校的 OpenAI 兼容端点）
- 新增一个共享的基金数据采集服务（AKShare 主 + 天天基金爬虫备），供上述模块读取基金净值和指数估值数据
- 新增 Docker Compose 部署方案（FastAPI + Next.js 静态构建 + Postgres + nginx + Let's Encrypt）
- 新增 GitHub Actions CI：`git push` 触发 VPS 自动部署
- 预置「懒人定投组合」seed 数据（3 只宽基基金 + 1 只货币基金默认配比），让不懂金融的用户开箱可用

## Capabilities

### New Capabilities

- `portfolio-tracking`: 录入基金交易记录、聚合持仓、计算成本/收益/收益率，展示收益曲线和持仓分布
- `valuation-monitoring`: 每日采集指数 PE/PB 数据，计算 10 年滚动百分位，可视化分位状态（低估/合理/高估）
- `investment-reminders`: 每月根据估值分位数规则动态计算定投金额，通过 SMTP 邮件推送给用户
- `news-aggregation`: 每日抓取持仓基金的公告/新闻，调用 OpenAI 兼容 LLM 生成摘要并展示

### Modified Capabilities

（无——这是一个全新项目，没有既有 spec 会被修改）

## Impact

- **代码**：全新仓库，零既有代码。新增 `backend/` (FastAPI + SQLAlchemy + APScheduler)、`frontend/` (Next.js App Router + Tailwind + Recharts)、`deploy/` (Docker Compose + nginx + Certbot)、`.github/workflows/` (CI)
- **外部依赖**：
  - 数据源：AKShare（免费 Python 库）+ 天天基金备用爬虫 + 中证指数官网
  - LLM：用户学校的 OpenAI 兼容 API 端点（通过 `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `MODEL_NAME` 环境变量配置）
  - SMTP：QQ 邮箱/Gmail 应用密码
- **基础设施**：用户自有国内云 2 核 2G 轻量服务器 (~100 元/年)，无 GPU 需求，Docker + nginx + Let's Encrypt
- **运维**：每日 19:00 定时抓取基金净值和估值数据；19:30 抓新闻 + LLM 摘要；每月 1 号 09:00 发送定投提醒邮件
- **安全**：单用户部署，通过 nginx basic auth 保护；`.env` 管理密钥，不入库；HTTPS 强制
- **明确不做**（YAGNI）：任何预测模型、实盘交易接口、多用户/注册登录、实时行情推送、本地部署 LLM
