#!/bin/sh
set -eu
DEPLOY_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
docker compose --env-file "$DEPLOY_ROOT/.env" -f "$DEPLOY_ROOT/docker-compose.yml" --profile certificates run --rm certbot renew --webroot -w /var/www/acme --quiet --deploy-hook "python /opt/export-certificates.py"
docker compose --env-file "$DEPLOY_ROOT/.env" -f "$DEPLOY_ROOT/docker-compose.yml" exec -T gateway nginx -t -c /tmp/nginx.conf
docker compose --env-file "$DEPLOY_ROOT/.env" -f "$DEPLOY_ROOT/docker-compose.yml" kill -s HUP gateway
