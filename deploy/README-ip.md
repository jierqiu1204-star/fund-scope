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

## 8. 本地构建和部署故障处理

### 8.1 Windows/Codex 静态构建长时间无输出

默认不要在 Windows/Codex 本地反复执行静态构建。当前项目的本地验证边界固定为：

```powershell
cd frontend
corepack pnpm exec tsc --noEmit

cd ..\backend
uv run python -m pytest <相关测试>
```

静态构建以服务器 Docker 构建为准：

```bash
cd /srv/fundscope/deploy
docker compose -f docker-compose.ip.yml up -d --build
```

如果本地必须执行静态构建，不要直接运行下面命令：

```powershell
corepack pnpm build:static
```

原因是 Codex/Windows 环境中断命令后，`next build` 的子进程可能残留为后台 `node.exe`，导致看起来“构建跑了十几分钟没有输出”。必须改用带超时和进程树清理的包装脚本：

```powershell
cd frontend
.\scripts\build-static-safe.ps1 -TimeoutSeconds 240
```

处理步骤固定为：

1. 停止当前命令。
2. 检查并结束遗留的 `node`、`pnpm`、`corepack`、`next` 进程；如果使用 `build-static-safe.ps1`，脚本超时后会调用 `taskkill /T /F` 清理子进程树。
3. 先跑本地轻量验证：

```powershell
cd frontend
corepack pnpm exec tsc --noEmit

cd ..\backend
uv run python -m pytest <相关测试>
```

4. 如果 TypeScript 和后端测试通过，但本地静态构建仍卡住，不继续死磕本地构建，以服务器 Docker 构建作为最终静态构建验证：

```bash
cd /srv/fundscope/deploy
docker compose -f docker-compose.ip.yml up -d --build
```

注意：本地沙箱卡住不等于可以跳过验证。至少需要完成 TypeScript 检查、后端相关测试、服务器 Docker 构建和页面验证；但不要让本地 `build:static` 无上限运行。

### 8.2 PowerShell 7 嵌套命令引号

Windows 上优先使用 PowerShell 7：

```powershell
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -Command 'corepack pnpm exec tsc --noEmit'
```

如果内部命令需要设置环境变量，外层 `-Command` 建议使用单引号，避免外层 PowerShell 提前展开 `$env:*` 或 `$_`：

```powershell
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -Command '$env:NEXT_TELEMETRY_DISABLED="1"; corepack pnpm build:static'
```

不要写成容易被外层提前解析的双引号形式：

```powershell
# 不推荐
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoLogo -NoProfile -Command "$env:NEXT_TELEMETRY_DISABLED='1'; corepack pnpm build:static"
```

### 8.3 Git/SSH 不可用时的归档部署 fallback

优先使用第 7 节的 `git pull` 更新。如果服务器 Git 凭据、远程仓库权限或 SSH 状态异常，可以改用“本地归档上传”：

本地生成归档：

```powershell
git rev-parse HEAD
git archive --format=tar HEAD -o .tmp/fundscope-deploy.tar
scp .tmp/fundscope-deploy.tar ubuntu@110.42.222.9:/tmp/fundscope-deploy.tar
```

服务器上更新：

```bash
sudo mkdir -p /srv/fundscope-backups
sudo cp -a /srv/fundscope /srv/fundscope-backups/fundscope-$(date +%Y%m%d-%H%M%S)
sudo mkdir -p /tmp/fundscope-new
sudo tar -xf /tmp/fundscope-deploy.tar -C /tmp/fundscope-new
sudo cp /srv/fundscope/.env /tmp/fundscope-new/.env
if [ -f /srv/fundscope/deploy/.env ]; then sudo cp /srv/fundscope/deploy/.env /tmp/fundscope-new/deploy/.env; fi
if [ -f /srv/fundscope/deploy/.htpasswd ]; then sudo cp /srv/fundscope/deploy/.htpasswd /tmp/fundscope-new/deploy/.htpasswd; fi
sudo rm -rf /srv/fundscope
sudo mv /tmp/fundscope-new /srv/fundscope
sudo chown -R ubuntu:ubuntu /srv/fundscope
```

记录部署版本：

```bash
cd /srv/fundscope
echo "<本地 git rev-parse HEAD 的提交号>" > .deploy-version
```

然后重建和迁移：

```bash
cd /srv/fundscope/deploy
docker compose -f docker-compose.ip.yml up -d --build
docker compose -f docker-compose.ip.yml exec backend alembic upgrade head
```

归档部署必须保留服务器本地配置文件，尤其是：

- `/srv/fundscope/.env`
- `/srv/fundscope/deploy/.env`
- `/srv/fundscope/deploy/.htpasswd`（如果当前部署仍使用）

这些文件可能包含数据库密码、SMTP 授权码、JWT 密钥等，不要写进 Git，也不要放进归档包。

## 9. 部署后完整验证清单

每次部署完成后至少检查：

```bash
cd /srv/fundscope/deploy
docker compose -f docker-compose.ip.yml ps
docker compose -f docker-compose.ip.yml exec backend alembic current
curl -f http://127.0.0.1/api/health
```

浏览器验证：

```text
http://110.42.222.9/login
http://110.42.222.9/short-term
http://110.42.222.9/admin/jobs
http://110.42.222.9/settings/notifications
```

如果本次涉及 ETF 研究链路，还需要手动检查：

- `/short-term` 能打开并显示 ETF 榜单。
- 我的持仓价格、行情时间和数据状态能正常显示。
- 后台任务页能看到最近任务记录。
- 如果有数据库迁移，`alembic current` 是最新 head。

## 10. 数据备份

```bash
cd /srv/fundscope/deploy
chmod +x backup-compose.sh
sudo ./backup-compose.sh
sudo ls -lh /var/backups/fundscope/
```

该脚本原子生成 PostgreSQL custom-format `.dump`，先执行
`pg_restore --list` 校验，再写入 `.sha256` 校验文件。正常 GitHub Actions
部署会在覆盖源码和执行迁移之前自动运行该脚本，并在同一目录记录
`rollback-metadata-*.txt`（旧提交、旧 schema head、备份路径和候选 head）。
恢复数据库属于显式事故处理操作，不由健康检查失败自动触发。

## 11. 重要限制

- IP 阶段是临时方案，不是最终生产安全方案。
- HTTP 没有传输加密，录入敏感真实资产信息前应尽快切换到域名 + HTTPS。
- FundScope 不连接支付宝、不连接券商、不自动下单，只做研究、追踪和提醒。
