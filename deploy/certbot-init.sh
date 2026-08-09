#!/usr/bin/env sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "Usage: CERTBOT_EMAIL=<email> ./certbot-init.sh <domain>"
  exit 1
fi

DOMAIN=$1
COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.ip.yml}
TIMEOUT_SECONDS=${TIMEOUT_SECONDS:-55}

case "${COMPOSE_FILE}" in
  docker-compose.ip.yml|docker-compose.yml) ;;
  *)
    echo "Unsupported COMPOSE_FILE: ${COMPOSE_FILE}" >&2
    exit 1
    ;;
esac

case "${DOMAIN}" in
  ""|.*|*.|*[!A-Za-z0-9.-]*)
    echo "Invalid domain: ${DOMAIN}" >&2
    exit 1
    ;;
esac

case "${TIMEOUT_SECONDS}" in
  ""|*[!0-9]*)
    echo "TIMEOUT_SECONDS must be an integer between 1 and 55" >&2
    exit 1
    ;;
esac
if [ "${TIMEOUT_SECONDS}" -lt 1 ] || [ "${TIMEOUT_SECONDS}" -gt 55 ]; then
  echo "TIMEOUT_SECONDS must be an integer between 1 and 55" >&2
  exit 1
fi

if [ -n "${CERTBOT_EMAIL:-}" ]; then
  timeout "${TIMEOUT_SECONDS}s" docker compose -f "${COMPOSE_FILE}" \
    run --rm certbot certonly \
    --webroot -w /var/www/certbot -d "${DOMAIN}" \
    --non-interactive --agree-tos --keep-until-expiring \
    --email "${CERTBOT_EMAIL}"
else
  timeout "${TIMEOUT_SECONDS}s" docker compose -f "${COMPOSE_FILE}" \
    run --rm certbot certonly \
    --webroot -w /var/www/certbot -d "${DOMAIN}" \
    --non-interactive --agree-tos --keep-until-expiring \
    --register-unsafely-without-email
fi
