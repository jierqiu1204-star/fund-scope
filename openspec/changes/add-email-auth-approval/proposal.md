## Why

FundScope 现在依赖 nginx Basic Auth，只有一个共享入口，无法区分不同用户、审批新账号，也无法把买入追踪提醒发到各自邮箱。随着短线 ETF/基金追踪变成主要使用场景，需要应用内账号体系来保护页面、隔离个人追踪，并让 qje 作为超级管理员审批账号。

## What Changes

- 新增邮箱注册/登录：用户使用邮箱和密码注册、登录，登录 token 存在前端 `localStorage`。
- 新增人工审批：注册用户默认不可用，只有 qje 超级管理员批准后才能进入业务页面。
- 新增超级管理员审批页：qje 登录后可以查看用户并批准或停用账号。
- 绑定用户邮箱：每个账号有自己的收件邮箱，默认等于注册邮箱，可在通知设置中修改。
- 追踪和提醒按用户隔离：买入追踪只归属创建者，卖出/减仓提醒发送到该用户绑定邮箱。
- **BREAKING**：IP/域名部署不再使用 nginx Basic Auth 作为主要访问保护，改为应用内登录保护。

## Capabilities

### New Capabilities

- `email-auth-approval`: 邮箱注册、登录、token 会话、账号审批、超级管理员权限和前端登录体验。

### Modified Capabilities

- `investment-reminders`: 通知设置和邮件发送需要按当前用户读取收件邮箱，后台任务入口需要超级管理员权限。
- `tracked-position-exit-strategy`: 追踪持仓和卖出/减仓提醒需要绑定用户，提醒邮件发送到持仓所属用户邮箱。

## Impact

- 后端：新增认证 API、鉴权依赖、用户表迁移、用户审批接口；调整追踪持仓、通知设置和后台任务的用户上下文。
- 前端：新增登录、注册、等待审批、用户审批页；API client 自动带 token；页面根据登录状态和管理员权限显示入口。
- 数据库：扩展 `users`，给 `tracked_positions` 增加 `user_id` 并将现有追踪归属 qje。
- 部署：新增 JWT secret 和 qje 启动管理员配置；移除 nginx Basic Auth 配置依赖。
- 依赖：增加密码哈希和 JWT 相关 Python 依赖。
