#!/usr/bin/env sh
set -eu

if [ "$#" -lt 1 ]; then
  echo "Usage: ./certbot-init.sh <domain>"
  exit 1
fi

docker compose run --rm certbot certonly --webroot -w /var/www/certbot -d "$1"
