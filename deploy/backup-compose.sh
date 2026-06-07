#!/usr/bin/env sh
set -eu

BACKUP_DIR=${BACKUP_DIR:-/var/backups/fundscope}
COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.ip.yml}
POSTGRES_USER=${POSTGRES_USER:-fundscope}
POSTGRES_DB=${POSTGRES_DB:-fundscope}

mkdir -p "${BACKUP_DIR}"

STAMP=$(date +%Y%m%d_%H%M%S)
docker compose -f "${COMPOSE_FILE}" exec -T postgres \
  pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" \
  > "${BACKUP_DIR}/fundscope_${STAMP}.sql"

find "${BACKUP_DIR}" -type f -name "fundscope_*.sql" -mtime +7 -delete
echo "${BACKUP_DIR}/fundscope_${STAMP}.sql"
