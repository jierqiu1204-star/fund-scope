#!/usr/bin/env sh

# Shared retention helpers for deployment and manual database backups.
# Callers must set BACKUP_DIR before sourcing this file.

BACKUP_KEEP_COUNT=${BACKUP_KEEP_COUNT:-3}
BACKUP_RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-7}
BACKUP_STALE_TMP_MINUTES=${BACKUP_STALE_TMP_MINUTES:-360}
BACKUP_MIN_FREE_BYTES=${BACKUP_MIN_FREE_BYTES:-2147483648}
BACKUP_ESTIMATED_BYTES=${BACKUP_ESTIMATED_BYTES:-3221225472}

backup_require_uint() {
  name=$1
  value=$2
  case "${value}" in
    ''|*[!0-9]*)
      echo "${name} must be a non-negative integer" >&2
      return 1
      ;;
  esac
}

backup_validate_retention_config() {
  : "${BACKUP_DIR:?BACKUP_DIR must be set}"
  backup_require_uint BACKUP_KEEP_COUNT "${BACKUP_KEEP_COUNT}"
  backup_require_uint BACKUP_RETENTION_DAYS "${BACKUP_RETENTION_DAYS}"
  backup_require_uint BACKUP_STALE_TMP_MINUTES "${BACKUP_STALE_TMP_MINUTES}"
  backup_require_uint BACKUP_MIN_FREE_BYTES "${BACKUP_MIN_FREE_BYTES}"
  backup_require_uint BACKUP_ESTIMATED_BYTES "${BACKUP_ESTIMATED_BYTES}"
  if [ "${BACKUP_KEEP_COUNT}" -lt 2 ]; then
    echo "BACKUP_KEEP_COUNT must be at least 2" >&2
    return 1
  fi
}

backup_acquire_lock() {
  exec 9>"${BACKUP_DIR}/.backup.lock"
  if ! flock -n 9; then
    echo "Another FundScope backup is already running" >&2
    return 1
  fi
}

backup_remove_pair() {
  dump_path=$1
  rm -f "${dump_path}.sha256" "${dump_path}"
}

backup_cleanup_stale_artifacts() {
  find "${BACKUP_DIR}" -maxdepth 1 -type f \
    -name 'fundscope_*.dump.tmp' \
    -mmin "+${BACKUP_STALE_TMP_MINUTES}" \
    -print -exec rm -f {} \; >&2

  for dump_path in "${BACKUP_DIR}"/fundscope_*.dump; do
    [ -f "${dump_path}" ] || continue
    [ ! -f "${dump_path}.sha256" ] || continue
    if find "${dump_path}" -prune \
      -mmin "+${BACKUP_STALE_TMP_MINUTES}" -print | grep -q .; then
      echo "Removing stale unsealed backup: ${dump_path}" >&2
      rm -f "${dump_path}"
    fi
  done

  for checksum_path in "${BACKUP_DIR}"/fundscope_*.dump.sha256; do
    [ -f "${checksum_path}" ] || continue
    dump_path=${checksum_path%.sha256}
    if [ ! -f "${dump_path}" ]; then
      echo "Removing orphan backup checksum: ${checksum_path}" >&2
      rm -f "${checksum_path}"
    fi
  done
}

backup_prune_completed() {
  keep_limit=$1
  backup_require_uint keep_limit "${keep_limit}"
  if [ "${keep_limit}" -lt 1 ]; then
    echo "keep_limit must be at least 1" >&2
    return 1
  fi

  list_path="${BACKUP_DIR}/.backup-retention.$$"
  trap 'rm -f "${list_path:-}"' EXIT HUP INT TERM
  if ! ls -1t "${BACKUP_DIR}"/fundscope_*.dump > "${list_path}" 2>/dev/null; then
    : > "${list_path}"
  fi

  index=0
  while IFS= read -r dump_path; do
    [ -f "${dump_path}" ] || continue
    if [ ! -f "${dump_path}.sha256" ]; then
      echo "Ignoring unsealed backup during retention: ${dump_path}" >&2
      continue
    fi

    index=$((index + 1))
    if [ "${index}" -eq 1 ]; then
      continue
    fi

    reason=
    if [ "${index}" -gt "${keep_limit}" ]; then
      reason="count>${keep_limit}"
    elif find "${dump_path}" -prune \
      -mtime "+${BACKUP_RETENTION_DAYS}" -print | grep -q .; then
      reason="age>${BACKUP_RETENTION_DAYS}d"
    fi

    if [ -n "${reason}" ]; then
      echo "Pruning backup (${reason}): ${dump_path}" >&2
      backup_remove_pair "${dump_path}"
    fi
  done < "${list_path}"

  rm -f "${list_path}"
  trap - EXIT HUP INT TERM
}

backup_latest_sealed_size() {
  list_path="${BACKUP_DIR}/.backup-size.$$"
  trap 'rm -f "${list_path:-}"' EXIT HUP INT TERM
  if ! ls -1t "${BACKUP_DIR}"/fundscope_*.dump > "${list_path}" 2>/dev/null; then
    : > "${list_path}"
  fi

  while IFS= read -r dump_path; do
    [ -f "${dump_path}.sha256" ] || continue
    wc -c < "${dump_path}" | tr -d ' '
    rm -f "${list_path}"
    trap - EXIT HUP INT TERM
    return 0
  done < "${list_path}"

  rm -f "${list_path}"
  trap - EXIT HUP INT TERM
  printf '%s\n' 0
}

backup_require_capacity() {
  previous_size=$(backup_latest_sealed_size)
  expected_size=${BACKUP_ESTIMATED_BYTES}
  if [ "${previous_size}" -gt "${expected_size}" ]; then
    expected_size=${previous_size}
  fi

  available_kb=$(df -Pk "${BACKUP_DIR}" | awk 'NR == 2 { print $4 }')
  backup_require_uint available_kb "${available_kb}"
  required_bytes=$((expected_size + BACKUP_MIN_FREE_BYTES))
  required_kb=$(((required_bytes + 1023) / 1024))

  if [ "${available_kb}" -lt "${required_kb}" ]; then
    echo "Insufficient backup capacity: available=${available_kb}KB required=${required_kb}KB" >&2
    return 1
  fi
}
