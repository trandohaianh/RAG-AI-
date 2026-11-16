#!/usr/bin/env bash
# Cập nhật bản mới trên VM (GitHub Actions gọi qua SSH, hoặc chạy tay).
set -euo pipefail
APP_DIR="${APP_DIR:-$HOME/fieldops}"
cd "$APP_DIR"
git fetch --prune origin
git reset --hard "origin/${DEPLOY_BRANCH:-main}"
cd deploy/cloud
set -a; source .env; set +a
# Backup trước khi đổi phiên bản (migration chạy lúc API khởi động).
docker compose --env-file .env exec -T api python -m pipeline.jobs.runner --only backup || true
docker compose --env-file .env up -d --build --remove-orphans
docker image prune -f >/dev/null
bash ./smoke.sh "https://$APP_DOMAIN"
