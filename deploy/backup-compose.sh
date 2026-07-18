#!/usr/bin/env sh
set -eu

BACKUP_DIR=${BACKUP_DIR:-/var/backups/fundscope}
COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.ip.yml}
POSTGRES_USER=${POSTGRES_USER:-fundscope}
POSTGRES_DB=${POSTGRES_DB:-fundscope}

mkdir -p "${BACKUP_DIR}"

STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="${BACKUP_DIR}/fundscope_${STAMP}.dump"
TMP_PATH="${BACKUP_PATH}.tmp"
trap 'rm -f "${TMP_PATH}"' EXIT

docker compose -f "${COMPOSE_FILE}" exec -T postgres \
  pg_dump -U "${POSTGRES_USER}" --format=custom "${POSTGRES_DB}" \
  > "${TMP_PATH}"
test -s "${TMP_PATH}"
docker compose -f "${COMPOSE_FILE}" exec -T postgres \
  pg_restore --list < "${TMP_PATH}" > /dev/null
mv "${TMP_PATH}" "${BACKUP_PATH}"
sha256sum "${BACKUP_PATH}" > "${BACKUP_PATH}.sha256"

find "${BACKUP_DIR}" -type f -name "fundscope_*.dump*" -mtime +7 -delete
echo "${BACKUP_PATH}"
