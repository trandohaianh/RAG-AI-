#!/usr/bin/env bash
# Cài FieldOps lên VM Ubuntu 22.04/24.04 (Oracle Cloud Always Free ARM hoặc bất kỳ VPS nào).
# Chạy trên VM:  curl -fsSL <raw-url>/deploy/cloud/setup-vm.sh | bash -s -- <git-repo-url>
#           hoặc: bash deploy/cloud/setup-vm.sh   (khi đã clone repo)
set -euo pipefail

REPO_URL="${1:-}"
APP_DIR="${APP_DIR:-$HOME/fieldops}"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

log "1/6 Cài Docker + công cụ"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi
sudo apt-get update -y && sudo apt-get install -y git curl openssl iptables-persistent

log "2/6 Mở cổng 80/443 (ảnh Ubuntu của Oracle chặn sẵn bằng iptables)"
for port in 80 443; do
  sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null \
    || sudo iptables -I INPUT 5 -p tcp --dport "$port" -j ACCEPT
done
sudo iptables -C INPUT -p udp --dport 443 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 5 -p udp --dport 443 -j ACCEPT
sudo netfilter-persistent save
echo "   Nhớ mở thêm 80, 443 trong Security List của VCN trên console Oracle."

log "3/6 Swap 2GB (build image trên 2 OCPU cần thêm bộ nhớ đệm)"
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

log "4/6 Lấy mã nguồn"
if [ -n "$REPO_URL" ] && [ ! -d "$APP_DIR/.git" ]; then
  git clone "$REPO_URL" "$APP_DIR"
fi
if [ -d "$APP_DIR/.git" ]; then cd "$APP_DIR"; else cd "$(dirname "$0")/../.."; APP_DIR="$(pwd)"; fi

log "5/6 Tạo deploy/cloud/.env"
ENV_FILE="$APP_DIR/deploy/cloud/.env"
if [ ! -f "$ENV_FILE" ]; then
  cp "$APP_DIR/deploy/cloud/.env.example" "$ENV_FILE"
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" "$ENV_FILE"
  sed -i "s|^METRICS_TOKEN=.*|METRICS_TOKEN=$(openssl rand -hex 24)|" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "   Đã tạo $ENV_FILE — hãy điền APP_DOMAIN, ACME_EMAIL, GEMINI_API_KEY, GOOGLE_CLIENT_ID rồi chạy lại script."
  exit 0
fi
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a
[ -n "${APP_DOMAIN:-}" ] || { echo "Thiếu APP_DOMAIN trong $ENV_FILE"; exit 1; }

if [ -n "${DUCKDNS_TOKEN:-}" ] && [[ "$APP_DOMAIN" == *.duckdns.org ]]; then
  sub="${APP_DOMAIN%.duckdns.org}"
  line="*/5 * * * * curl -fsS 'https://www.duckdns.org/update?domains=${sub}&token=${DUCKDNS_TOKEN}&ip=' >/dev/null 2>&1"
  ( crontab -l 2>/dev/null | grep -v duckdns.org; echo "$line" ) | crontab -
  curl -fsS "https://www.duckdns.org/update?domains=${sub}&token=${DUCKDNS_TOKEN}&ip=" && echo "   DuckDNS cập nhật IP"
fi

log "6/6 Build và chạy"
cd "$APP_DIR/deploy/cloud"
sudo docker compose --env-file .env up -d --build
bash "$APP_DIR/deploy/cloud/smoke.sh" "https://$APP_DOMAIN" || true
echo
echo "Xong. Mở https://$APP_DOMAIN — tài khoản đăng ký đầu tiên là admin."
