# FundScope 单人实例与历史账号兼容

一个部署实例对应一个使用者，不需要登录、注册、qje 审批或 JWT 密钥。所有能访问该实例的设备共享持仓、收件邮箱和任务操作权限。数据库及历史账号记录保留，不需要清库或合并持仓。

升级时优先通过根目录 `.env` 的 `INSTANCE_OWNER_EMAIL` 选择已有账号；未配置时兼容旧 `AUTH_BOOTSTRAP_ADMIN_EMAIL`，再选最早的管理员或账号。已有自定义邮件设置不会被覆盖。旧库如果有多个账号，保留明确选择，避免显示到另一个账号的持仓：

```env
INSTANCE_OWNER_EMAIL=<原来登录使用的邮箱>
```

此值必须对应数据库里已有的账号；新安装无需填写。旧 `AUTH_JWT_SECRET`、`AUTH_TOKEN_EXPIRE_DAYS`、管理员显示名和密码已不参与运行。未配置过的迁移种子账号会使用本实例 SMTP 配置，不再使用历史迁移内的固定邮箱。

自动提醒从根目录 `.env` 读取 `SMTP_PASSWORD`，收件邮箱在 `/settings/notifications` 设置。网页上的测试密码只用于当次测试，不会写入数据库或 `.env`。更改 `.env` 后需要重新创建后端容器。

Compose 默认只监听 `127.0.0.1`。远程使用 SSH 隧道、私有网络或限制来源的代理；`FUNDSCOPE_BIND_ADDRESS` 放在 `deploy/.env`（Compose 配置），与后端根目录 `.env` 分开。
