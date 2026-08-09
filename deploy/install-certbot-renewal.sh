#!/usr/bin/env sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer as root" >&2
  exit 1
fi

DEPLOY_DIR=${DEPLOY_DIR:-/srv/fundscope/deploy}
CRON_FILE=/etc/cron.d/fundscope-certbot-renew

case "${DEPLOY_DIR}" in
  /srv/fundscope/deploy) ;;
  *)
    echo "Refusing unexpected DEPLOY_DIR: ${DEPLOY_DIR}" >&2
    exit 1
    ;;
esac

test -x "${DEPLOY_DIR}/certbot-renew.sh"

cat > "${CRON_FILE}" <<EOF
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
17 3 * * * root cd ${DEPLOY_DIR} && flock -n /run/lock/fundscope-certbot-renew.lock ${DEPLOY_DIR}/certbot-renew.sh >> /var/log/fundscope-certbot-renew.log 2>&1
EOF

chmod 0644 "${CRON_FILE}"
echo "Installed ${CRON_FILE}"
