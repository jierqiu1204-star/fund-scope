# FundScope VPS Deployment

This is the domain + HTTPS deployment path. If you need to deploy temporarily by
public IP before buying or configuring a domain, use `README-ip.md` instead.

## Services

- `postgres`: application database
- `backend`: FastAPI API and APScheduler jobs
- `frontend-static-builder`: Next.js static export builder
- `nginx`: static hosting and `/api/` reverse proxy. Access control is handled by the FundScope application login.
- `certbot`: certificate issuance helper

## Required Local Files

Create these files on the deployment host. Do not commit private values.

- `/srv/fundscope/.env`: copy from `.env.example` and replace database, OpenAI, SMTP, JWT, and bootstrap admin placeholders.
- `POSTGRES_PASSWORD`: export in the shell or provide through a host-level environment file before running Compose.
- `FQDN`: export the public hostname used by nginx and Certbot.

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
the VPS. It checks out the pushed commit, syncs it to `/srv/fundscope`, preserves
server-local configuration files, then runs Compose from `/srv/fundscope/deploy`.
The server checkout does not need to be a git repository.

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
3. Export `POSTGRES_PASSWORD=<strong-password>` and `FQDN=<your-domain>`.
4. Ensure `.env` contains the application login settings:

   ```bash
   AUTH_JWT_SECRET=<random-long-secret>
   AUTH_TOKEN_EXPIRE_DAYS=30
   AUTH_BOOTSTRAP_ADMIN_EMAIL=19535838578@163.com
   AUTH_BOOTSTRAP_ADMIN_DISPLAY_NAME=qje
   AUTH_BOOTSTRAP_ADMIN_PASSWORD=<qje-login-password>
   ```

5. Build and start the local stack:

   ```bash
   docker compose up -d --build
   ```

6. Issue the first TLS certificate:

   ```bash
   ./certbot-init.sh <domain>
   ```

7. Run migrations:

   ```bash
   docker compose exec backend alembic upgrade head
   ```

8. Visit `/login`, log in as the bootstrap admin, then open `/onboarding` if default data still needs seeding.

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
- Application login: open `/login`, sign in as the bootstrap admin, and confirm `/admin/users` is visible.
- Backend health: `curl -f https://$FQDN/api/health`.
- nginx proxying: log in through the browser, then confirm business pages load their `/api/` data.
- Static frontend: open `/portfolio`, `/valuation`, `/news`, `/short-term`, and `/recommendations`.
- Scheduler: temporarily set one job to run in the next few minutes, then check `docker compose logs -f backend` and `/api/admin/jobs`.
- Backups: run `./backup.sh`, confirm a new dump exists under `/var/backups/fundscope/`, and confirm old dumps rotate.
- Recovery note: document the latest successful commit SHA and database backup path.

## Runtime Operations

- Manual job trigger: `POST /api/admin/jobs/{job_name}/run`
- Logs: `docker compose logs -f backend nginx`
- Routine code deploy: push to GitHub and wait for the `deploy.yml` workflow.
- SSH/manual Compose is reserved for incident response, first-time setup, runner
  maintenance, migrations or one-off production jobs that are not part of the
  normal deploy workflow.
- Manual rebuild only when intentionally bypassing the GitHub runner:
  `docker compose up -d --build`
- Rollback: check out the previous commit, rebuild, and restore the latest known-good database dump if needed.
