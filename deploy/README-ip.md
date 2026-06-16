# FundScope IP 临时部署指南

本指南用于先不买域名、直接用服务器公网 IP 访问 FundScope：

```text
http://110.42.222.9
```

裸 IP 通常不能申请 Let’s Encrypt 正常证书，所以这个方案暂时使用 HTTP。安全边界依赖两层：nginx Basic Auth 网页密码，以及云服务器安全组或 `ufw` 只允许你的当前公网 IP 访问 80 端口。后续有域名后，再切回 `docker-compose.yml` + `nginx.conf` + Certbot 的 HTTPS 方案。

## 1. 服务器安全初始化

截图里已经暴露过服务器 IP 和密码，先在云厂商控制台重置服务器密码。然后登录服务器，创建普通部署用户：

```bash
sudo adduser fundscope
sudo usermod -aG sudo fundscope
```

配置 SSH 密钥登录后，再考虑关闭 root 密码登录。确认 `fundscope` 用户能 SSH 登录之前，不要关闭当前可用登录方式。

建议云服务器安全组只开放：

```text
22/tcp  你的电脑公网 IP
80/tcp  你的电脑公网 IP
```

如果系统启用了 `ufw`，可以这样设置：

```bash
sudo ufw allow from <你的电脑公网IP> to any port 22 proto tcp
sudo ufw allow from <你的电脑公网IP> to any port 80 proto tcp
sudo ufw enable
sudo ufw status
```

## 2. 安装基础软件

用 `fundscope` 用户登录服务器后安装 Docker、Compose 和 Git：

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

重新登录一次，让 `docker` 用户组生效：

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

如果代码已经在服务器上，以后更新用：

```bash
cd /srv/fundscope
git pull
```

## 4. 创建配置文件

先创建 Compose 插值用的 `deploy/.env`。这里的密码只给 PostgreSQL 容器用，不要提交到 Git：

```bash
cd /srv/fundscope/deploy
cat > .env <<'EOF'
POSTGRES_PASSWORD=<换成一个新的强数据库密码>
NEXT_PUBLIC_API_BASE_URL=
# 留空时默认同源 /api；如需固定接口源可显式写 IP 或域名
EOF
```

再创建后端运行用的 `/srv/fundscope/.env`。`DATABASE_URL` 里的密码必须和上面的 `POSTGRES_PASSWORD` 一致：

```bash
cd /srv/fundscope
cat > .env <<'EOF'
DATABASE_URL=postgresql+asyncpg://fundscope:<同一个数据库密码>@postgres:5432/fundscope
CORS_ORIGINS=http://110.42.222.9
OPENAI_BASE_URL=https://api.example.com/v1
OPENAI_API_KEY=replace-me
MODEL_NAME=gpt-4o-mini
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=user@example.com
SMTP_PASSWORD=replace-me
SMTP_FROM=FundScope <user@example.com>
NGINX_BASIC_AUTH_USER=fundscope
NGINX_BASIC_AUTH_PASS=replace-me
EOF
```

第一版不配置真实 OpenAI 和 SMTP 也能打开网页、跑回测和模拟盘；新闻摘要和邮件提醒会受影响。

## 5. 创建网页登录密码

Basic Auth 是访问网页时弹出的用户名和密码。不要复用服务器 SSH 密码。

```bash
cd /srv/fundscope/deploy
chmod +x create-htpasswd.sh
./create-htpasswd.sh fundscope <换成网页登录密码>
```

会生成：

```text
/srv/fundscope/deploy/.htpasswd
```

## 6. 启动服务

所有 Compose 命令都在 `deploy/` 目录下执行：

```bash
cd /srv/fundscope/deploy
docker compose -f docker-compose.ip.yml up -d --build
docker compose -f docker-compose.ip.yml ps
```

执行数据库迁移：

```bash
docker compose -f docker-compose.ip.yml exec backend alembic upgrade head
```

然后用浏览器打开：

```text
http://110.42.222.9
```

首次进入后打开：

```text
http://110.42.222.9/onboarding
```

初始化默认基金池。

## 7. 部署后验证

在服务器上验证后端健康状态：

```bash
curl -u fundscope:<网页登录密码> http://127.0.0.1/api/health
```

在浏览器验证：

```text
http://110.42.222.9/admin/jobs
http://110.42.222.9/strategy-lab
```

建议先在 `/admin/jobs` 点：

```text
回填 365 天净值
更新指数估值
运行筛选评分
更新所有模拟盘
```

## 8. 日常更新

以后代码更新后：

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

## 9. 数据备份

执行备份：

```bash
cd /srv/fundscope/deploy
chmod +x backup-compose.sh
sudo ./backup-compose.sh
```

备份文件默认在：

```text
/var/backups/fundscope/
```

查看备份：

```bash
sudo ls -lh /var/backups/fundscope/
```

## 10. 重要限制

- IP 阶段是临时方案，不是最终生产安全方案。
- HTTP 传输没有加密，安全性依赖“只允许你的公网 IP 访问 80 端口”。
- 不要在公网不受限的 HTTP 页面里录入敏感真实资产信息。
- 有域名后，改用 HTTPS 部署方案。
- FundScope 不连接支付宝，不自动真实下单；模拟盘只是虚拟记录。
