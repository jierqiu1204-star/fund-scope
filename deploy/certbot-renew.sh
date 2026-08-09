#!/usr/bin/env sh
set -eu

COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.yml}
TIMEOUT_SECONDS=${TIMEOUT_SECONDS:-55}

case "${COMPOSE_FILE}" in
  docker-compose.yml) ;;
  *)
    echo "Certificate renewal requires docker-compose.yml" >&2
    exit 1
    ;;
esac

case "${TIMEOUT_SECONDS}" in
  ""|*[!0-9]*)
    echo "TIMEOUT_SECONDS must be a positive integer" >&2
    exit 1
    ;;
esac
if [ "${TIMEOUT_SECONDS}" -lt 1 ] || [ "${TIMEOUT_SECONDS}" -gt 55 ]; then
  echo "TIMEOUT_SECONDS must be an integer between 1 and 55" >&2
  exit 1
fi

timeout "${TIMEOUT_SECONDS}s" docker compose -f "${COMPOSE_FILE}" run --rm certbot \
  renew --webroot -w /var/www/certbot --quiet
timeout 10s docker compose -f "${COMPOSE_FILE}" exec -T nginx nginx -s reload
