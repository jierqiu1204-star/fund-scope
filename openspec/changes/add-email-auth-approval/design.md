## Context

FundScope 当前没有应用层登录，生产访问主要依赖 nginx Basic Auth。后端已有 `users` 表用于通知设置，但缺少密码、审批和管理员字段；买入追踪 `tracked_positions` 没有 `user_id`，列表、详情和提醒任务按全局数据处理，邮件收件人固定读取 `User(1)`。前端 `frontend/lib/api.ts` 只配置 baseURL，没有 token 注入或登录守卫。

## Goals / Non-Goals

**Goals:**

- 用邮箱+密码提供应用内注册、登录和 30 天 JWT 会话。
- 注册账号默认未批准，只有 qje 超级管理员审批后可使用业务页面。
- qje 账号绑定 `19535838578@163.com`，批准字段为 true，并拥有用户审批和后台任务权限。
- 追踪持仓和通知设置按用户隔离，卖出/减仓邮件发给持仓所属用户的绑定邮箱。
- 移除部署层 Basic Auth 作为主要访问保护，避免双重登录。

**Non-Goals:**

- 不做邮箱验证码、找回密码、多因素认证或第三方 OAuth。
- 不接支付宝、券商或自动交易。
- 不在本次隔离资产账本、策略实验室、市场数据、短线研究榜单；它们保持共享数据源和共享研究结果。

## Decisions

- **使用 JWT Bearer token，前端存 `localStorage`。** 这是用户明确要求，适合当前单页应用。后端新增 `get_current_user`、`require_approved_user`、`require_super_admin` 依赖。替代方案是 HttpOnly Cookie，安全性更好但会牵涉 CSRF、跨域和部署配置，本次不采用。
- **登录标识使用邮箱，qje 作为显示名和超级管理员身份。** 注册表单只要求邮箱、密码和可选昵称，避免维护用户名登录的额外唯一性逻辑。
- **密码只存哈希，启动管理员密码走环境变量。** 增加 `AUTH_JWT_SECRET`、`AUTH_TOKEN_EXPIRE_DAYS`、`AUTH_BOOTSTRAP_ADMIN_EMAIL`、`AUTH_BOOTSTRAP_ADMIN_DISPLAY_NAME`、`AUTH_BOOTSTRAP_ADMIN_PASSWORD`。迁移负责字段和数据回填，启动补齐逻辑负责按 env 创建/更新 qje 密码哈希，避免把明文密码写入 Git 或 migration。
- **审批和后台操作只允许超级管理员。** `/api/admin/*`、用户审批页、数据更新按钮和后台任务运行都要求 `is_super_admin=true`。普通已批准用户可以看短线研究、创建自己的追踪、设置自己的收件邮箱。
- **仅隔离追踪和通知设置。** `tracked_positions.user_id` 回填到 qje，所有追踪 CRUD 按当前用户过滤；定时/盘中提醒任务遍历所有 active 追踪，但发送邮件时读取 `position.user_id` 对应用户。市场数据和排名继续共享，避免重复拉取和重复计算。
- **部署移除 nginx Basic Auth。** nginx 继续静态托管和反向代理 `/api/`，但不再配置 `auth_basic`。鉴权在 FastAPI 和前端路由守卫中完成。

## Risks / Trade-offs

- **localStorage token 被 XSS 读取风险** → 保持页面不渲染未信任 HTML，token 有 30 天过期，后续如需要可升级为 HttpOnly Cookie。
- **未批准用户已拿不到 token 但注册接口暴露公网** → 注册只创建不可用账号，后台审批页展示待审批用户；后续可加频率限制。
- **历史追踪归属迁移错误** → migration 明确将所有现有 `tracked_positions` 归属 qje，并在测试中验证。
- **移除 Basic Auth 后 API 完全依赖应用鉴权** → 所有非公开业务路由统一加认证依赖；健康检查可保持公开，admin 路由必须 super admin。
- **启动管理员密码 env 缺失导致 qje 无法登录** → 服务器部署步骤必须设置 bootstrap admin env；启动日志给出中文警告，不在代码内硬编码密码。

## Migration Plan

1. 新增 Alembic migration：扩展 `users` 字段、给 `tracked_positions` 增加 `user_id`、回填 qje 用户和现有追踪归属。
2. 增加启动补齐逻辑：根据 env 创建/更新 qje 超级管理员密码哈希和邮箱。
3. 上线后运行迁移，设置 `AUTH_JWT_SECRET` 和 qje bootstrap env。
4. 移除 nginx Basic Auth 并重建部署。
5. 验证 qje 可登录、审批页可用、现有追踪仍显示、提醒邮件发到所属用户邮箱。

Rollback 需要恢复 Basic Auth 配置并回滚应用版本；数据库新增字段可保留，不影响旧版本读取原有核心字段。
