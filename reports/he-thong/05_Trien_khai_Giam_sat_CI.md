# Hệ thống 5: Triển khai, giám sát và CI

**Phạm vi:**

- `docker-compose.yml` (gốc)
- `deploy/cloud/` (Caddyfile, compose, `deploy.sh`, `setup-vm.sh`, `smoke.sh`)
- `deploy/production/` (nginx, compose nhiều dịch vụ, bootstrap)
- `render.yaml`, `railway.toml`
- `monitoring/` (alert rules, dashboard Grafana)
- `.github/workflows/`
- `backend/metrics.py`

---

## 1. Các biến thể triển khai

| Biến thể | Thành phần | Dùng khi |
|---|---|---|
| `deploy/cloud` | Caddy (TLS tự động) → `api` (uvicorn), `web` (Next standalone), `pipeline` (scheduler), Prometheus/Grafana; một volume `fieldops_data` dùng chung | Một VM (Oracle Ampere), triển khai qua `deploy.sh` từ GitHub Actions |
| `deploy/production` | Gateway nginx, Postgres TLS, Redis ACL, Kafka, secrets qua `_FILE`; kèm hai dự án khác (`edu/`, `telecom/`) | Topology nhiều dịch vụ chạy cục bộ hoặc on-premise |
| `render.yaml` / `railway.toml` | API (Docker) cùng Postgres được quản lý | PaaS dùng thử |

---

## 2. Luồng và cơ chế

### 2.1 Edge, TLS và header bảo mật (`deploy/cloud/Caddyfile`)

- Header chung cho toàn site: HSTS, `nosniff` và Referrer-Policy.
- **Chống nhúng trang (F1, đã sửa):**
  - `X-Frame-Options: SAMEORIGIN` cho mọi path **trừ** `/embed`.
  - `/embed` nhận `Content-Security-Policy: frame-ancestors *`.

  Trước đây XFO áp cho toàn site, nên widget trả phí trên website khách chỉ là ô trắng. Bản `deploy/production/nginx.conf` vốn đã để `/embed` nhúng được.
- `/api/*` proxy tới `api:8000` với `flush_interval -1` (SSE không bị đệm) và read timeout 10 phút.
- Body tối đa 30 MB.

### 2.2 Cấu hình bắt buộc (đã sửa)

- **Cloud compose:** `ACME_EMAIL: ${ACME_EMAIL:?…}`. `deploy.sh` cũng kiểm tra `APP_DOMAIN` và `ACME_EMAIL` trước khi `up`. Trước đây biến rỗng biến dòng `email {$ACME_EMAIL}` thành lỗi cú pháp Caddyfile và cả site sập.
- **`render.yaml` (D7):** thêm `APP_ENV=production` và `METRICS_TOKEN` (generateValue).
- **`railway.toml`:** ghi chú bắt buộc phải đặt `APP_ENV`, `SECRET_KEY`, `METRICS_TOKEN` và `CORS_ORIGINS`.

Thiếu `APP_ENV` thì API chạy ở chế độ dev: `/metrics` mở công khai và các kiểm tra cấu hình production bị bỏ qua.

### 2.3 Giám sát

- **Endpoint `/metrics`:** cần `METRICS_TOKEN` ở production, so sánh theo thời gian hằng.
- **Collector đọc DB ở mỗi lượt scrape:**
  - trạng thái và thời điểm thành công gần nhất của từng job;
  - số vi phạm chất lượng dữ liệu;
  - dung lượng đĩa;
  - **mới:** `fieldops_field_webhook_deliveries{status}`.
- **Alert `FieldOpsWebhookDeadLetter`** (U9, đã sửa): giờ dùng `sum(delta(fieldops_field_webhook_deliveries{status="dead"}[1h])) > 0`. Counter cũ chỉ tăng trong container pipeline, mà container đó không được scrape.

### 2.4 CI (`.github/workflows/ci.yml`)

- **Các job:**
  - backend: pytest trên Python 3.11 và 3.12, với service Postgres 16;
  - 3 cổng eval offline;
  - promtool;
  - `docker compose config`;
  - frontend: typecheck, build và `npm audit`;
  - client-state test;
  - mobile: `tsc`;
  - build image đa kiến trúc.
- **Bước "Validate docker compose" (F2, đã sửa):** chạy `python -B deploy/production/bootstrap_local.py` trước `docker compose config`. File `docker-compose.yml` gốc `include` tới `deploy/production/.env`, file này bị gitignore và cần secret do bootstrap sinh ra, nên trước đây job này **luôn đỏ** trên checkout sạch.

---

## 3. Lỗi đã sửa

| ID | Mức | Vị trí | Kịch bản lỗi trước khi sửa | Kiểm chứng |
|---|---|---|---|---|
| F1 | **Cao** | `deploy/cloud/Caddyfile` | Widget nhúng không hiển thị trên website khách. | Đọc cấu hình; header `frame-ancestors` của Next kiểm bằng `curl` (Hệ thống 6). Chưa chạy Caddy thật. |
| F2 | **Cao** | `.github/workflows/ci.yml` | Bước compose của CI luôn thất bại. | Trên bản archive: `exit=1` trước bootstrap, `exit=0` sau bootstrap |
| U18b | Thấp | `deploy/cloud/docker-compose.yml`, `deploy.sh` | `ACME_EMAIL` rỗng làm Caddy không khởi động được. | `docker compose config`: báo lỗi rõ ràng khi thiếu, PASS khi có; `bash -n deploy.sh` OK; `round2::test_cloud_compose_requires_acme_email` |
| D7 | TB | `render.yaml`, `railway.toml` | Render và Railway chạy ở chế độ dev. | `round2::test_render_blueprint_runs_the_api_in_production_mode` |
| U9 | Thấp–TB | `metrics.py`, `monitoring/alerts/fieldops-rules.yml` | Alert dead-letter không kích hoạt. | `round2::test_dead_letter_gauge_is_exported_by_the_api`; YAML parse được 12 rule |

---

## 4. Hạn chế còn lại

- **Hai cấu hình edge (đã có chốt chặn ở vòng 4):** `tests/test_edge_config_parity.py` so khớp header nền, Permissions-Policy, chính sách khung `/embed` và việc tắt buffer SSE giữa Caddy và nginx. Lần so khớp đầu tiên phát hiện nginx gửi `geolocation=()`, chặn nút "lấy vị trí hiện tại" của màn điều phối. Đã sửa: cả hai gửi `camera=(self), geolocation=(self)`.
- **`deploy/production`** đóng gói chung với hai dự án khác; nên tách repo hoặc dùng submodule.
- **Chưa chạy được cục bộ:** Caddy thật, promtool và Docker daemon. Sau khi deploy, nên kiểm bằng:

  ```bash
  curl -I https://<domain>/embed
  ```

  ```bash
  curl -I https://<domain>/
  ```

  `/embed` phải không có XFO và có `frame-ancestors *`; trang `/` phải có XFO.

---

## 5. Vùng đã xác minh là đúng

- Compose production: chỉ gateway publish cổng; các mạng dữ liệu là `internal`; Redis tắt user `default`.
- Secret đi qua Docker secrets hoặc `_FILE`, và giá trị được kiểm định dạng hex trước khi dùng trong SQL hay ACL.
- Shell script dùng `set -euo pipefail` và đặt biến trong dấu nháy.
- `deploy.yml` chỉ chạy khi CI pass, và có concurrency group.
- nginx và Caddy đều ghi đè `X-Forwarded-For`.
