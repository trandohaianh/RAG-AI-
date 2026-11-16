#!/bin/sh
set -eu
DEPLOY_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
docker compose --env-file "$DEPLOY_ROOT/.env" -f "$DEPLOY_ROOT/docker-compose.yml" exec -T gateway logrotate --state /var/log/nginx/.logrotate-state /etc/logrotate.d/ai-systems
