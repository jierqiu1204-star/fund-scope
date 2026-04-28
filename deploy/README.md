# FundScope VPS Deployment

## Services

- `postgres`: application database
- `backend`: FastAPI API and APScheduler jobs
- `frontend-static-builder`: Next.js static export builder
- `nginx`: static hosting, reverse proxy, and basic auth
- `certbot`: certificate issuance helper

## Required Local Files

Create these files on the deployment host. Do not commit private values.

- `/srv/fundscope/.env`: copy from `.env.example` and replace database, OpenAI, SMTP, and auth placeholders.
- `/srv/fundscope/deploy/.htpasswd`: generate with `./create-htpasswd.sh <user> <pass>`.
- `POSTGRES_PASSWORD`: export in the shell or provide through a host-level environment file before running Compose.
- `FQDN`: export the public hostname used by nginx and Certbot.

## GitHub Actions Secrets

Configure these in GitHub: repository `Settings` -> `Secrets and variables` -> `Actions`.

- `VPS_HOST`: DNS name or IP address of the deployment host.
- `VPS_USER`: SSH user that owns or can access `/srv/fundscope`.
- `VPS_SSH_KEY`: private key with permission to SSH to the host. Store the full PEM text.

Readiness check:

```bash
gh secret list
```

The list should include `VPS_HOST`, `VPS_USER`, and `VPS_SSH_KEY`. The values are intentionally not printed.

## First Deploy

1. Clone the repository to `/srv/fundscope`.
2. Copy `.env.example` to `.env` and replace placeholder values.
3. Export `POSTGRES_PASSWORD=<strong-password>` and `FQDN=<your-domain>`.
4. Generate basic-auth credentials:

   ```bash
   cd /srv/fundscope/deploy
   ./create-htpasswd.sh <user> <pass>
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

8. Visit `/onboarding` and apply the default portfolio seed.

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
- Basic auth: unauthenticated `curl -I https://$FQDN` returns `401`, and authenticated `curl -I -u <user>:<pass> https://$FQDN` reaches the app.
- Backend health: `curl -f -u <user>:<pass> https://$FQDN/api/health`.
- nginx proxying: `curl -f -u <user>:<pass> https://$FQDN/api/valuation/current`.
- Static frontend: open `/portfolio`, `/valuation`, `/news`, and `/recommendations`.
- Scheduler: temporarily set one job to run in the next few minutes, then check `docker compose logs -f backend` and `/api/admin/jobs`.
- Backups: run `./backup.sh`, confirm a new dump exists under `/var/backups/fundscope/`, and confirm old dumps rotate.
- Recovery note: document the latest successful commit SHA and database backup path.

## Runtime Operations

- Manual job trigger: `POST /api/admin/jobs/{job_name}/run`
- Logs: `docker compose logs -f backend nginx`
- Rebuild after pulling a new revision: `docker compose up -d --build`
- Rollback: check out the previous commit, rebuild, and restore the latest known-good database dump if needed.
