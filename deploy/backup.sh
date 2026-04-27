#!/usr/bin/env sh
set -eu

BACKUP_DIR=/var/backups/fundscope
mkdir -p "${BACKUP_DIR}"

STAMP=$(date +%Y%m%d_%H%M%S)
pg_dump "${DATABASE_URL}" > "${BACKUP_DIR}/fundscope_${STAMP}.sql"
find "${BACKUP_DIR}" -type f -mtime +7 -delete
