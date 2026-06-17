# FundScope IP 临时部署指南

本指南用于不使用域名时的临时部署，访问地址：

```text
http://110.42.222.9
```

IP 阶段使用 HTTP，浏览器传输没有 HTTPS 加密。安全边界依赖两层：

- 云服务器安全组或 `ufw` 只允许可信来源访问 `22` 和 `80`。
- FundScope 应用内邮箱登录和人工审批。

当前版本不再使用 nginx Basic Auth；不要再创建或依赖 `deploy/.htpasswd`。

## 1. 服务器安全初始化

建议创建普通部署用户，避免长期使用 root：

```bash
sudo adduser fundscope
sudo usermod -aG sudo fundscope
```

建议安全组只开放：

```text
22/tcp  你的电脑公网 IP
80/tcp  你的电脑公网 IP
```

如果启用 `ufw`：

```bash
sudo ufw allow from <你的电脑公网IP> to any port 22 proto tcp
sudo ufw allow from <你的电脑公网IP> to any port 80 proto tcp
sudo ufw enable
sudo ufw status
```

## 2. 安装基础软件

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg git
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker fundscope
```

重新登录后检查：

```bash
docker version
docker compose version
```

## 3. 拉取代码

```bash
sudo mkdir -p /srv/fundscope
sudo chown -R fundscope:fundscope /srv/fundscope
git clone <你的Git仓库地址> /srv/fundscope
cd /srv/fundscope
```

已有代码时：

```bash
cd /srv/fundscope
git pull
```

## 4. 创建配置

先创建 Compose 使用的 `deploy/.env`：

```bash
cd /srv/fundscope/deploy
cat > .env <<'EOF'
POSTGRES_PASSWORD=<换成强数据库密码>
NEXT_PUBLIC_API_BASE_URL=
EOF
```

再创建后端运行用的 `/srv/fundscope/.env`：

```bash
cd /srv/fundscope
cat > .env <<'EOF'
DATABASE_URL=postgresql+asyncpg://fundscope:<同一个数据库密码>@postgres:5432/fundscope
CORS_ORIGINS=http://110.42.222.9,http://taslr2.xyz
OPENAI_BASE_URL=https://api.example.com/v1
OPENAI_API_KEY=replace-me
MODEL_NAME=gpt-4o-mini
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=user@example.com
SMTP_PASSWORD=replace-me
SMTP_FROM=FundScope <user@example.com>
AUTH_JWT_SECRET=<换成随机长字符串>
AUTH_TOKEN_EXPIRE_DAYS=30
AUTH_BOOTSTRAP_ADMIN_EMAIL=19535838578@163.com
AUTH_BOOTSTRAP_ADMIN_DISPLAY_NAME=qje
AUTH_BOOTSTRAP_ADMIN_PASSWORD=<qje登录密码>
EOF
```

`AUTH_BOOTSTRAP_ADMIN_PASSWORD` 只放在服务器 `.env`，不要提交到 Git。

## 5. 启动服务

所有 Compose 命令都在 `deploy/` 目录执行：

```bash
cd /srv/fundscope/deploy
docker compose -f docker-compose.ip.yml up -d --build
docker compose -f docker-compose.ip.yml exec backend alembic upgrade head
docker compose -f docker-compose.ip.yml ps
```

打开：

```text
http://110.42.222.9/login
```

使用 `AUTH_BOOTSTRAP_ADMIN_EMAIL` 和 `AUTH_BOOTSTRAP_ADMIN_PASSWORD` 登录。新用户注册后默认不可用，需要 qje 到 `/admin/users` 批准。

## 6. 部署后验证

```bash
curl -f http://127.0.0.1/api/health
```

浏览器验证：

```text
http://110.42.222.9/login
http://110.42.222.9/short-term
http://110.42.222.9/admin/jobs
http://110.42.222.9/admin/users
```

## 7. 日常更新

```bash
cd /srv/fundscope
git pull
cd deploy
docker compose -f docker-compose.ip.yml up -d --build
docker compose -f docker-compose.ip.yml exec backend alembic upgrade head
docker compose -f docker-compose.ip.yml ps
```

查看日志：

```bash
docker compose -f docker-compose.ip.yml logs -f backend nginx
```

## 8. 数据备份

```bash
cd /srv/fundscope/deploy
chmod +x backup-compose.sh
sudo ./backup-compose.sh
sudo ls -lh /var/backups/fundscope/
```

## 9. 重要限制

- IP 阶段是临时方案，不是最终生产安全方案。
- HTTP 没有传输加密，录入敏感真实资产信息前应尽快切换到域名 + HTTPS。
- FundScope 不连接支付宝、不连接券商、不自动下单，只做研究、追踪和提醒。
