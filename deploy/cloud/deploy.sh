#!/usr/bin/env bash
# Cập nhật bản mới trên VM (GitHub Actions gọi qua SSH, hoặc chạy tay).
set -euo pipefail
APP_DIR="${APP_DIR:-$HOME/fieldops}"
cd "$APP_DIR"
git fetch --prune origin
# DEPLOY_SHA (workflow deploy truyền vào) = đúng commit CI đã kiểm; chạy tay thì lấy đầu nhánh.
git reset --hard "${DEPLOY_SHA:-origin/${DEPLOY_BRANCH:-main}}"
cd deploy/cloud
set -a; source .env; set +a
# Caddyfile dùng `email {$ACME_EMAIL}`: biến rỗng làm Caddy lỗi cú pháp và cả site sập.
: "${APP_DOMAIN:?Thiếu APP_DOMAIN trong deploy/cloud/.env}"
: "${ACME_EMAIL:?Thiếu ACME_EMAIL trong deploy/cloud/.env (email nhận thông báo chứng chỉ)}"
# Mỗi dự án tự có HTTPS: mặc định Caddy chỉ phục vụ FieldOps. SHARED_EDGE=1 bật chế độ tuỳ chọn
# "edge dùng chung" (Caddy này phục vụ thêm site của dự án khác trên cùng VM, tiết kiệm một VM).
FILES=(-f docker-compose.yml)
if [ "${SHARED_EDGE:-0}" = "1" ]; then
  FILES+=(-f docker-compose.shared-edge.yml)
  docker network inspect edge >/dev/null 2>&1 || docker network create edge
  sudo mkdir -p /opt/edge/sites 2>/dev/null || mkdir -p /opt/edge/sites
fi
compose() { docker compose --env-file .env "${FILES[@]}" "$@"; }

# Backup trước khi đổi phiên bản (migration chạy lúc API khởi động).
compose exec -T api python -m pipeline.jobs.runner --only backup || true
# Giữ bản đang chạy để quay lại được nếu bản mới hỏng smoke test.
for image in fieldops-api fieldops-web; do
  if docker image inspect "$image:local" >/dev/null 2>&1; then docker tag "$image:local" "$image:previous"; fi
done
compose build
# API chạy bằng uid 1000 (không phải root): volume tạo khi còn chạy root cần chown đúng một lần.
compose run --rm --no-deps --user root --entrypoint sh api   -c '[ "$(stat -c %u /app/data)" = 1000 ] || chown -R 1000:1000 /app/data'
compose up -d --remove-orphans
if ! bash ./smoke.sh "https://$APP_DOMAIN"; then
  echo "Smoke test thất bại — quay lại bản trước." >&2
  if docker image inspect fieldops-api:previous >/dev/null 2>&1; then
    docker tag fieldops-api:previous fieldops-api:local
    if docker image inspect fieldops-web:previous >/dev/null 2>&1; then docker tag fieldops-web:previous fieldops-web:local; fi
    compose up -d --no-build --remove-orphans
    bash ./smoke.sh "https://$APP_DOMAIN" || true
    echo "Nếu bản mới đã chạy migration CSDL, bản cũ sẽ từ chối khởi động vì gặp migration lạ:" >&2
    echo "khôi phục app.db từ bản backup vừa tạo trong /app/data/backups (xem deploy/cloud/README.md)." >&2
  fi
  exit 1
fi
docker image prune -f >/dev/null
