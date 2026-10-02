# FundScope IP 临时部署指南

本指南用于个人实例部署，不需要登录、注册或管理员审批。默认只在服务器 `127.0.0.1:80` 监听，从电脑通过 SSH 隧道访问；无需对公网开放应用端口。能访问同一实例的设备共享全部数据和操作权限。

如果使用私有网络或来源受限的代理，可在 `deploy/.env` 配置 `FUNDSCOPE_BIND_ADDRESS`。直接绑定公网地址时，必须先限制允许访问的来源；应用没有账号隔离。HTTP 访问请通过 SSH 隧道或可信加密网络。当前版本不使用 nginx Basic Auth。

## 1. 服务器安全初始化

建议创建普通部署用户，避免长期使用 root：

```bash
sudo adduser fundscope
sudo usermod -aG sudo fundscope
```

建议安全组只开放：

```text
22/tcp  你的电脑公网 IP
```

如果启用 `ufw`：

```bash
sudo ufw allow from <你的电脑公网IP> to any port 22 proto tcp
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
FUNDSCOPE_BIND_ADDRESS=127.0.0.1
EOF
```

再创建后端运行用的 `/srv/fundscope/.env`：

```bash
cd /srv/fundscope
cat > .env <<'EOF'
DATABASE_URL=postgresql+asyncpg://fundscope:<同一个数据库密码>@postgres:5432/fundscope
CORS_ORIGINS=http://localhost:8080
OPENAI_BASE_URL=https://api.example.com/v1
OPENAI_API_KEY=replace-me
MODEL_NAME=gpt-4o-mini
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=user@example.com
SMTP_PASSWORD=replace-me
SMTP_FROM=FundScope <user@example.com>
EOF
```

数据库密码、SMTP 授权码和 API 密钥只放在本机 `.env`，不要提交到 Git。收件邮箱在网页设置中配置，网页测试密码不会保存。升级已有多账号数据库时，用 `INSTANCE_OWNER_EMAIL=<原账号邮箱>` 保留选定持仓（详见 `README-auth.md`）。

## 5. 启动服务

所有 Compose 命令都在 `deploy/` 目录执行：

```bash
cd /srv/fundscope/deploy
docker compose -f docker-compose.ip.yml up -d --build
docker compose -f docker-compose.ip.yml exec backend alembic upgrade head
docker compose -f docker-compose.ip.yml ps
```

在自己的电脑建立 SSH 隧道（连接保持打开）：

```bash
ssh -N -L 8080:127.0.0.1:80 <部署用户>@<服务器IP>
```

然后直接打开 `http://localhost:8080/short-term` 和 `http://localhost:8080/settings/notifications`。

## 6. 部署后验证

```bash
curl -f http://127.0.0.1/api/health
```

浏览器验证：

```text
http://localhost:8080/short-term
http://localhost:8080/admin/jobs
http://localhost:8080/settings/notifications
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

这些文件可能包含数据库密码、SMTP 授权码、API 密钥等，不要写进 Git，也不要放进归档包。

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
http://localhost:8080/short-term
http://localhost:8080/admin/jobs
http://localhost:8080/settings/notifications
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

- 仅供个人实例使用，同一实例没有多用户数据隔离。
- HTTP 本身没有传输加密；远程访问使用 SSH 隧道、可信加密网络或来源受限的 HTTPS 代理。
- FundScope 不连接支付宝、不连接券商、不自动下单，只做研究、追踪和提醒。
