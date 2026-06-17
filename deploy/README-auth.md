# FundScope 应用内登录配置

本项目现在使用应用内邮箱登录和 qje 管理员审批，不再依赖 nginx Basic Auth。

在服务器 `/srv/fundscope/.env` 中增加：

```bash
AUTH_JWT_SECRET=<换成随机长字符串>
AUTH_TOKEN_EXPIRE_DAYS=30
AUTH_BOOTSTRAP_ADMIN_EMAIL=19535838578@163.com
AUTH_BOOTSTRAP_ADMIN_DISPLAY_NAME=qje
AUTH_BOOTSTRAP_ADMIN_PASSWORD=<qje登录密码>
```

部署后执行：

```bash
cd /srv/fundscope/deploy
docker compose -f docker-compose.ip.yml up -d --build
docker compose -f docker-compose.ip.yml exec backend alembic upgrade head
```

打开站点后使用 `AUTH_BOOTSTRAP_ADMIN_EMAIL` 和 `AUTH_BOOTSTRAP_ADMIN_PASSWORD` 登录。新用户注册后默认不可用，需要 qje 在 `/admin/users` 批准。
