# FundScope VPS Deployment

This is the domain + HTTPS deployment path. If you need to deploy temporarily by
public IP before buying or configuring a domain, use `README-ip.md` instead.

## Services

- `postgres`: application database
- `backend`: FastAPI API and APScheduler jobs
- `frontend-static-builder`: Next.js static export builder
- `nginx`: static hosting and `/api/` reverse proxy. This personal instance has no application login; Compose listens on loopback by default.
- `certbot`: certificate issuance helper

## Required Local Files

Create these files on the deployment host. Do not commit private values.

- `/srv/fundscope/.env`: copy from `.env.example` and replace database, OpenAI, and SMTP placeholders.
- `POSTGRES_PASSWORD`: set it in `/srv/fundscope/deploy/.env`.
- `FQDN`: set the hostname in `/srv/fundscope/deploy/.env` for nginx and Certbot.
- `FUNDSCOPE_BIND_ADDRESS`: defaults to `127.0.0.1`; set in `deploy/.env` only for a trusted private interface or a deployment with network access restrictions. Every reachable client can read and change this instance.

## GitHub Actions Runner

Automatic deployment uses a GitHub self-hosted runner on the VPS. The runner must
be registered to this repository with the `fundscope-vps` label and must be able
to run `sudo docker compose` without an interactive password prompt.

Readiness check:

```bash
gh api repos/jierqiu1204-star/fund-scope/actions/runners
```

The runner list should include an `online` runner named `fundscope-vps`.

## Automatic Deploys

Pushes to the GitHub `codex-strategy-lab` branch trigger `.github/workflows/deploy.yml`.

Use this as the normal release path: commit locally, push to GitHub, then let
GitHub Actions deploy on the VPS runner. Do not SSH to the server for routine
code deployment after every change.

The workflow runs on the `fundscope-vps` GitHub self-hosted runner installed on
the VPS. It downloads the exact commit's source archive from GitHub, validates
and extracts it before replacing the runner workspace. It then syncs the source
to `/srv/fundscope`, preserves server-local configuration files, and runs Compose
from `/srv/fundscope/deploy`.
The server checkout does not need to be a git repository.

Before building, the workflow removes unused `fundscope-*` images, unused
Docker build cache and dangling images. It skips images referenced by any
container, including stopped containers, and image removal is not forced.
Database and active research containers retain their images. If the frontend
changes, an unreferenced previous static-builder image is removed before its
replacement is built; the exported frontend volume is retained. After checking
the candidate schema head, the workflow releases the temporary candidate image
and build cache before backing up. It also cleans the candidate on failure.
Database volumes and sealed backups are retained.
The backup still requires the larger of the configured estimate (5 GiB by
default) or the latest sealed dump, plus 2 GiB of free space. If that check
fails, use the disk, Docker, database and backup-size diagnostics in the job log
to decide whether more storage is needed; source replacement has not started.

Server-local files preserved across each deploy:

- `/srv/fundscope/.env`
- `/srv/fundscope/deploy/.env`
- `/srv/fundscope/deploy/.htpasswd`, if it exists

It removes the previous Compose containers with:

```bash
sudo docker compose -f "$COMPOSE_FILE" down --remove-orphans
```

It does not pass `-v`, so database and certificate volumes are retained.

Optional GitHub Actions repository variables:

- `VPS_COMPOSE_FILE`: Compose file under `deploy/`. Defaults to `docker-compose.yml`. Set to `docker-compose.ip.yml` for the temporary IP deployment.

## First Deploy

1. Clone the repository to `/srv/fundscope`.
2. Copy `.env.example` to `.env` and replace placeholder values.
3. Set `POSTGRES_PASSWORD=<strong-password>` and `FQDN=<your-domain>` in
   `/srv/fundscope/deploy/.env`.
4. Restrict access using a private network, SSH tunnel, or trusted proxy. When upgrading a database with multiple old accounts, set `INSTANCE_OWNER_EMAIL` in the root `.env` to its existing owner email (see `README-auth.md`). New instances need no account configuration.

5. Bootstrap HTTP with the IP Compose file. This keeps the application online
   while serving the ACME challenge from the shared Certbot volume:

   ```bash
   docker compose -f docker-compose.ip.yml up -d --build
   ```

6. Issue the first TLS certificate:

   ```bash
   CERTBOT_EMAIL=<email> ./certbot-init.sh <domain>
   ```

   The HTTP challenge path must be reachable by the certificate authority through your external proxy; a loopback-only listener by itself cannot complete it. For private access without an existing TLS proxy, use the IP guide with an SSH tunnel instead.

7. Only after certificate issuance succeeds, switch to the domain stack and
   install bounded daily renewal:

   ```bash
   docker compose -f docker-compose.ip.yml down --remove-orphans
   docker compose -f docker-compose.yml up -d
   sudo ./install-certbot-renewal.sh
   ```

8. Run migrations:

   ```bash
   docker compose -f docker-compose.yml exec backend alembic upgrade head
   ```

9. Open `/short-term` through your trusted connection and configure `/settings/notifications`. Open `/onboarding` if default data still needs seeding.

## Local Compose Verification

Run from `deploy/`:

```bash
docker compose up -d --build
docker compose ps
curl -f http://localhost/health
curl -f http://localhost/api/health
curl -I http://localhost/portfolio
curl -I http://localhost/valuation
curl -I http://localhost/news
curl -I http://localhost/recommendations
```

If startup is blocked, record the failing command, full output, and next action in `docs/mvp-verification.md`.

## VPS Verification Checklist

After deployment, verify these items before calling the release ready:

- HTTPS: `curl -I https://$FQDN` returns a 2xx or 3xx response with a valid certificate.
- Personal instance: open `/short-term` and `/settings/notifications` without signing in, and confirm the expected holdings and recipient.
- Backend health: `curl -f https://$FQDN/api/health`.
- nginx proxying: confirm business pages load their `/api/` data through the trusted connection.
- Static frontend: open `/portfolio`, `/valuation`, `/news`, `/short-term`, and `/recommendations`.
- Scheduler: temporarily set one job to run in the next few minutes, then check `docker compose logs -f backend` and `/api/admin/jobs`.
- Backups: every `deploy.yml` run creates an atomic custom-format dump before
  source replacement, validates it with `pg_restore --list`, writes a SHA-256
  sidecar, and retains it under `/var/backups/fundscope/` for seven days.
- Recovery note: use the matching `rollback-metadata-*.txt` to recover the
  previous commit SHA, schema head, and validated database backup path.

## Runtime Operations

- Manual job trigger: `POST /api/admin/jobs/{job_name}/run`
- Logs: `docker compose logs -f backend nginx`
- Routine code deploy: push to GitHub and wait for the `deploy.yml` workflow.
- SSH/manual Compose is reserved for incident response, first-time setup, runner
  maintenance, migrations or one-off production jobs that are not part of the
  normal deploy workflow.
- Manual rebuild only when intentionally bypassing the GitHub runner:
  `docker compose up -d --build`
- Rollback: use the previous commit and validated dump recorded in the matching
  rollback metadata. Database restoration remains an explicit incident-response
  action; do not overwrite production automatically after a failed health check.
