## Context

FundScope 当前有应用级邮箱注册/登录、用户审批、qje bootstrap super admin，以及追踪持仓按用户隔离。市场研究数据本身是公共行情/公开基金数据，它不应该按用户复制；用户私有数据主要是“我买了什么、买入价、提醒记录、通知邮箱”。

用户现在需要 qje 管理员可以从用户列表排查其他账号情况：例如某个用户是否已批准、绑定了什么邮箱、追踪了哪些资产、有没有触发提醒、邮件是否发送失败。但管理员查看必须是只读，不能代用户修改持仓或提醒，避免误操作和责任边界混乱。

## Goals / Non-Goals

**Goals:**
- 明确全局市场数据与用户私有追踪数据的边界。
- 给 super admin 提供用户列表里的只读详情入口。
- 管理员可查看用户追踪持仓、估算盈亏、持仓处理状态、最近提醒、邮件发送状态。
- 管理员可查看邮件配置状态摘要，但不能查看授权码明文。
- 保持普通用户只能访问和修改自己的追踪数据。

**Non-Goals:**
- 不做管理员代操作用户持仓。
- 不做多租户数据隔离到独立数据库。
- 不新增角色体系；只使用现有 `is_super_admin`。
- 不改变短线排序和行情数据生成逻辑。
- 不改变邮件提醒触发规则。

## Decisions

### 1. 市场研究数据全局共享

ETF 行情、日线、短线排序、AI/规则研究报告、观察组合都属于公开市场研究数据。所有已批准用户看到同一套数据。这样避免重复计算和不同用户看到不同市场结论。

用户隔离只作用于：tracked positions、alerts、notification settings、recipient email、account approval/status。

### 2. 新增 admin 只读 API 命名空间

建议新增：
- `GET /api/admin/users`：复用或扩展现有用户审批列表。
- `GET /api/admin/users/{user_id}`：账号概况。
- `GET /api/admin/users/{user_id}/tracked-positions`：该用户追踪持仓快照，只读。
- `GET /api/admin/users/{user_id}/alerts`：该用户最近提醒记录，只读。

这些接口只允许 `current_user.is_super_admin`。普通用户访问返回 403。

### 3. 不复用普通写接口做管理员查看

普通接口如 `PATCH /api/tracked-positions/{id}`、`POST /close` 继续只允许 owner。管理员只读页面不得调用这些写接口，也不提供“编辑买入价”“停止追踪”等按钮。

### 4. 敏感字段脱敏

管理员详情可以显示：
- 用户 id、邮箱、display name、批准状态、super admin 状态、创建时间、最后登录时间（如已有）。
- recipient_email。
- SMTP 配置状态：已配置/未配置、host、port、username 脱敏、from email。

不能显示：
- password hash。
- SMTP password / 授权码。
- JWT token。
- 服务器密钥或 API key。

### 5. 只读访问可审计

第一版可以先记录应用日志；如果已有审计表则写审计表。至少在服务日志里记录 admin user id、target user id、访问接口、时间。后续再扩展完整审计。

### 6. UI 放在现有管理员用户列表后面

用户列表增加“查看”按钮，打开用户详情页或抽屉。详情页分区：
- 账号信息。
- 邮件配置状态。
- 当前追踪持仓。
- 最近提醒/邮件状态。
- 数据边界说明：市场研究数据全局共享，以下内容是该用户私有追踪数据。

## Risks / Trade-offs

- [Risk] 管理员看到用户投资信息涉及隐私。→ Mitigation: 仅 super admin 可读，页面标注只读，后端记录访问日志。
- [Risk] UI 误放写按钮导致误操作。→ Mitigation: 使用单独只读组件，不复用带编辑按钮的普通追踪卡。
- [Risk] 敏感配置泄露。→ Mitigation: API response schema 不包含 password hash、SMTP password、token；测试覆盖。
- [Risk] 管理员读接口绕过 owner scope。→ Mitigation: 所有 admin read 必须走独立 dependency `require_super_admin`，普通业务接口仍按 owner scope。

## Migration Plan

1. 不改现有数据结构即可实现第一版。
2. 后端新增只读 admin API。
3. 前端用户管理页加只读详情入口。
4. 加权限和脱敏测试。
5. 部署后用 qje 验证能看用户详情，普通用户验证不能访问。

## Open Questions

- 是否需要记录完整审计表？第一版建议先写后端日志，后续如多人使用再建表。
- 用户详情页用新页面还是抽屉？建议新页面 `/admin/users/{id}`，便于刷新和排查。
- 是否显示估算盈亏金额？建议显示，因为管理员排查提醒链路需要它，但必须标注估算和数据来源。
