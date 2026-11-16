# Đưa FieldOps lên mạng miễn phí — hướng dẫn từng bước

Kiến trúc: **1 VM Oracle Cloud Always Free** (ARM, 2 OCPU, 12 GB RAM) chạy Docker Compose
gồm `api` (FastAPI), `pipeline` (job hằng ngày), `web` (Next.js), `caddy` (HTTPS tự động).
Monitoring dùng **Grafana Cloud free**; CI, eval hằng đêm và deploy dùng **GitHub Actions**.

```
Người dùng ──HTTPS──▶ Caddy ─┬─ /api/*  ──▶ api (FastAPI) ──▶ SQLite + Chroma (volume fieldops_data)
                              └─ /*      ──▶ web (Next.js)          ▲
                                                pipeline (02:00 UTC) ┘  KPI · data quality · retention · backup
Grafana Cloud ──scrape 60s, Bearer token──▶ /api/metrics
GitHub Actions: CI + eval offline ▶ deploy qua SSH ▶ smoke test · eval live hằng đêm · uptime mỗi giờ
```

Tổng chi phí: **$0/tháng**. Gemini free tier tính $0 (trang Chi phí hiển thị giá trị quy đổi
theo giá paid tier). Nếu muốn tên miền riêng thay DuckDNS: khoảng $10/năm.

> Giới hạn free cần biết (kiểm tra 11/2026):
> - **Oracle**: từ 15/06/2026 gói Always Free cho ARM chỉ còn 2 OCPU / 12 GB (trước đây là 4/24).
>   Cần thẻ Visa/Mastercard để xác minh. Một số region hay báo hết máy ARM.
> - **Grafana Cloud free**: 10.000 active series, giữ dữ liệu 14 ngày.
> - **Email**: dùng Gmail SMTP với App Password (không cần domain riêng, Google giới hạn khoảng
>   vài trăm email/ngày cho tài khoản thường). Resend free (3.000 email/tháng, 100/ngày) chỉ dùng được
>   khi có domain riêng đã xác minh.
> - **GitHub Actions**: repo private được 2.000 phút/tháng; repo public không giới hạn.

---

## Bước 1 — Tạo VM Oracle Cloud (≈20 phút)

1. Đăng ký tại <https://signup.cloud.oracle.com> và chọn **Home Region** gần Việt Nam
   (Singapore hoặc Tokyo). Home Region không đổi được về sau.
2. Vào **Compute → Instances → Create instance**:
   - Image: **Canonical Ubuntu 24.04**
   - Shape: **Ampere → VM.Standard.A1.Flex**, chọn **2 OCPU, 12 GB**
   - SSH keys: tải lên public key của bạn (`ssh-keygen -t ed25519` nếu chưa có)
   - Boot volume: 50–100 GB
3. Nếu báo *Out of capacity*: thử 1 OCPU / 6 GB, hoặc thử lại sau vài giờ.
4. Vào **Networking → VCN → Security List** và thêm 2 Ingress rule: TCP 80 và TCP 443, nguồn
   `0.0.0.0/0`.
5. Ghi lại **Public IP** của VM.

## Bước 2 — Tên miền miễn phí bằng DuckDNS (≈3 phút)

1. Đăng nhập <https://www.duckdns.org> bằng GitHub.
2. Tạo subdomain, ví dụ `fieldops-haianh`, rồi điền Public IP của VM vào.
3. Copy **token** để dùng ở bước 5.

## Bước 3 — Google Sign-In (≈5 phút)

1. Vào <https://console.cloud.google.com> → **APIs & Services → OAuth consent screen**: chọn
   External, điền tên app và email, rồi **Publish app** để ai cũng đăng nhập được.
2. Vào **Credentials → Create credentials → OAuth client ID → Web application**:
   - Authorized JavaScript origins: `https://fieldops-haianh.duckdns.org` (thêm
     `http://localhost:3000` nếu muốn thử ở máy local).
   - Không cần Redirect URI vì app dùng popup Google Identity Services.
3. Copy **Client ID** (dạng `....apps.googleusercontent.com`).

## Bước 4 — Gemini API key và email

1. Lấy Gemini key tại <https://aistudio.google.com/apikey>.
2. Email (để xác thực tài khoản và quên mật khẩu): tạo một Gmail riêng cho hệ thống, bật xác minh
   2 bước, rồi tạo **App Password** tại <https://myaccount.google.com/apppasswords>. Điền
   `SMTP_USER` (địa chỉ Gmail) và `SMTP_PASSWORD` (16 ký tự) vào `.env`.

## Bước 5 — Cài lên VM (≈15 phút, phần lớn là thời gian build)

```bash
ssh ubuntu@<PUBLIC_IP>
git clone https://github.com/trandohaianh/RAG-AI-.git ~/fieldops
bash ~/fieldops/deploy/cloud/setup-vm.sh     # lần 1: cài Docker, mở firewall, tạo .env rồi dừng
nano ~/fieldops/deploy/cloud/.env           # điền APP_DOMAIN, ACME_EMAIL, GEMINI_API_KEY,
                                            # GOOGLE_CLIENT_ID, DUCKDNS_TOKEN
bash ~/fieldops/deploy/cloud/setup-vm.sh     # lần 2: build + chạy + smoke test
```

Smoke test phải in ra 6 dòng ✓. Sau đó mở `https://<APP_DOMAIN>` và đăng ký tài khoản đầu tiên.
**Tài khoản đầu tiên tự động là admin.**

> Repo private: `git clone` cần Personal Access Token (quyền read) hoặc deploy key.

## Bước 6 — Monitoring bằng Grafana Cloud (≈10 phút)

1. Tạo stack free tại <https://grafana.com>.
2. Vào **Connections → Add new connection → Metrics Endpoint**:
   - Scrape URL: `https://<APP_DOMAIN>/api/metrics`
   - Authentication: **Bearer**, giá trị là `METRICS_TOKEN` trong file `.env` trên VM
     (`grep METRICS_TOKEN ~/fieldops/deploy/cloud/.env`)
   - Bấm Test connection rồi Save. Grafana Cloud sẽ scrape mỗi 60 giây.
3. Vào **Dashboards → New → Import**, tải lên file
   `monitoring/grafana/dashboards/fieldops-production.json` và chọn datasource Prometheus của stack.
4. Cảnh báo: vào **Alerting → Contact points** để thêm email của bạn. Sau đó tạo alert rule từ
   `monitoring/alerts/fieldops-rules.yml`. Có 2 cách: tạo tay từng rule trong UI, hoặc nạp cả file
   bằng `mimirtool rules load` (cần access policy có quyền `rules:write`). Có 10 rule: API sập,
   5xx > 5%, p95 > 3s, chi phí AI > $1/giờ, model lỗi, dò mật khẩu, pipeline/backup trễ hơn 26 giờ,
   dữ liệu lỗi, ổ đĩa sắp đầy.
5. (Khuyến nghị) Thêm một monitor 5 phút trên UptimeRobot hoặc Better Stack cho
   `https://<APP_DOMAIN>/api/health/ready`.

## Bước 7 — Tự động hoá bằng GitHub Actions

Vào **Settings → Secrets and variables → Actions** của repo và thêm:

| Loại | Tên | Giá trị |
|---|---|---|
| Secret | `DEPLOY_HOST` | Public IP của VM |
| Secret | `DEPLOY_USER` | `ubuntu` |
| Secret | `DEPLOY_SSH_KEY` | Private key để SSH vào VM |
| Secret | `GEMINI_API_KEY` | Key cho eval live hằng đêm |
| Variable | `PUBLIC_URL` | `https://<APP_DOMAIN>` |

Các workflow:
- **CI** (`ci.yml`): pytest, eval offline (chặn merge nếu tụt chất lượng), build web, kiểm tra
  alert rules và compose.
- **Deploy** (`deploy.yml`): khi CI xanh trên `main` thì SSH vào VM chạy `deploy/cloud/deploy.sh`.
  Script này backup trước, build lại, rồi chạy smoke test.
- **Eval (live Gemini)** (`evals-live.yml`): 02:30 giờ Việt Nam mỗi đêm. Kết quả xem trong tab
  Summary, report tải về ở mục Artifacts.
- **Uptime** (`uptime.yml`): gọi `/api/health/ready` mỗi giờ. Nếu lỗi, GitHub gửi email.

## Vận hành hằng ngày

```bash
cd ~/fieldops/deploy/cloud
docker compose --env-file .env ps                          # trạng thái
docker compose --env-file .env logs -f api                 # log API
docker compose --env-file .env exec api python -m pipeline.jobs.runner            # chạy pipeline ngay
docker compose --env-file .env exec api python -m pipeline.jobs.runner --backfill 30
bash smoke.sh https://$APP_DOMAIN
```

- **Backup** nằm ở `/app/data/backups` trong volume `fieldops_data`, giữ 7 bản: SQLite (online
  backup) và Chroma (`tar.gz`). Nên chép định kỳ ra ngoài VM, ví dụ bằng
  `docker cp fieldops-api-1:/app/data/backups ./` rồi `rclone` lên Google Drive.
- **Chi phí**: trang *Chi phí* trong app (chỉ admin thấy). Đặt `LLM_DAILY_BUDGET_USD` để hệ thống
  tự từ chối yêu cầu khi đã dùng hết ngân sách ngày.
- **Xác thực email**: khi SMTP đã gửi được (thử bằng "Quên mật khẩu"), đặt
  `REQUIRE_EMAIL_VERIFICATION=true` rồi chạy lại `docker compose up -d`. Người đăng nhập Google
  được coi là đã xác thực.
- **App mobile (Expo)**: tạo thêm OAuth client loại iOS và/hoặc Android trong Google Cloud. Android
  cần package name và SHA-1. Đặt `EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID` / `EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID`
  (và `EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID` cho bản web) khi build app. Phía server, thêm các client ID
  này vào `GOOGLE_CLIENT_ID`, phân tách bằng dấu phẩy.

## Khi không lấy được VM Oracle

Bộ compose chạy trên mọi VPS Linux có ≥2 GB RAM (Hetzner, DigitalOcean, Vultr…). Chỉ cần chạy
`setup-vm.sh` như ở bước 5. Render/Koyeb free không phù hợp: chỉ 512 MB RAM, tự ngủ sau 15 phút
và không có ổ đĩa lưu lâu dài cho Chroma.
