#!/usr/bin/env sh
set -eu

BACKUP_DIR=${BACKUP_DIR:-/var/backups/fundscope}
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "${SCRIPT_DIR}/backup-retention.sh"

mkdir -p "${BACKUP_DIR}"
backup_validate_retention_config
backup_acquire_lock
backup_cleanup_stale_artifacts
PREVIOUS_KEEP_COUNT=$((BACKUP_KEEP_COUNT - 1))
backup_prune_completed "${PREVIOUS_KEEP_COUNT}"
backup_require_capacity

STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="${BACKUP_DIR}/fundscope_${STAMP}.dump"
TMP_PATH="${BACKUP_PATH}.tmp"
trap 'rm -f "${TMP_PATH}"' EXIT

pg_dump --format=custom "${DATABASE_URL}" > "${TMP_PATH}"
test -s "${TMP_PATH}"
pg_restore --list < "${TMP_PATH}" > /dev/null
mv "${TMP_PATH}" "${BACKUP_PATH}"
sha256sum "${BACKUP_PATH}" > "${BACKUP_PATH}.sha256"

backup_prune_completed "${BACKUP_KEEP_COUNT}"
backup_cleanup_stale_artifacts
echo "${BACKUP_PATH}"
