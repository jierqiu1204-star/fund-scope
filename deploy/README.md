# FundScope VPS Deployment

## Services

- `postgres`: application database
- `backend`: FastAPI API + scheduler
- `frontend-static-builder`: Next.js static export builder
- `nginx`: static hosting, reverse proxy, basic auth
- `certbot`: certificate issuance helper

## First Deploy

1. Copy `.env.example` to `.env` and set production credentials.
2. Set `POSTGRES_PASSWORD` in the shell or an override file.
3. Export `FQDN=<your-domain>` and generate `.htpasswd` with `./create-htpasswd.sh <user> <pass>`.
4. Build and start the stack with `docker compose up -d --build`.
5. Create the first certificate with `./certbot-init.sh <domain>`.
5. Run migrations from the backend container:
   - `docker compose exec backend alembic upgrade head`
6. Visit `/onboarding` and apply the default portfolio seed.

## Runtime Operations

- Manual job trigger: `POST /api/admin/jobs/{job_name}/run`
- Backups: schedule `backup.sh` daily via cron
- Logs: `docker compose logs -f backend nginx`

## Migration Plan

- Keep a daily `pg_dump` in `/var/backups/fundscope`
- Deploy new revisions with `docker compose up -d --build`
- Roll back by checking out the previous commit and rebuilding
