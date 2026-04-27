#!/usr/bin/env sh
set -eu

if [ "$#" -lt 2 ]; then
  echo "Usage: ./create-htpasswd.sh <username> <password>"
  exit 1
fi

docker run --rm --entrypoint htpasswd httpd:2-alpine -Bbn "$1" "$2" > .htpasswd
