# FieldOps — Báo cáo sẵn sàng production (Production Readiness Report)

- **Ngày:** 2026-11-17
- **Nhánh:** `fullstack/fieldops-production`
- **Mốc so sánh:** `4d7068a`
- **Vai trò rà soát:** Tech Lead full-stack, DevOps, QA.
- **Phạm vi:** backend FastAPI, frontend Next 16, cơ sở dữ liệu và migration, Docker / Compose / Caddy / nginx, Render / Railway, GitHub Actions.
- **Ràng buộc:** không mở `.env` thật, `TAI-KHOAN.md`, `evaluation.local` hay `*.docx`, và không in bí mật.
  - Mọi kiểm tra cấu hình dùng file `*.env.example` hoặc biến giả.

---

## 1. Trạng thái tổng

### 🟡 NEEDS ATTENTION — mã sẵn sàng, cấu hình triển khai cần chủ hệ thống làm checklist ở mục 6

- **Không còn Blocker nào trong mã.**
  - Test: backend 606 passed, 12 skipped. Frontend build được ở cả hai chế độ. Client test 23/23.
  - E2E qua HTTP thật: 17/17 luồng chính, 16/16 luồng biên.
  - Khởi động ở chế độ production đã kiểm.
- **Sẵn sàng cho dry run trên máy local** (chạy uvicorn trực tiếp; bước `docker compose up --build` chưa chạy được ở đây, xem dưới).
- **Chưa thể ghi "Ready" vì ba lý do:**
  1. **Docker daemon không chạy trên máy rà soát** (Docker Desktop đã cài nhưng tắt). Vì vậy:
     - compose chỉ được kiểm tra tĩnh bằng `docker compose config --quiet`: cloud, production và production+gemini đều hợp lệ;
     - **chưa build image thật.**
  2. Một số giá trị chỉ chủ hệ thống có (khoá Gemini, tên miền, URL công khai trên Render/Railway).
  3. Hai điều chỉnh cần kiểm tra trên bản deploy thật: `TRUSTED_PROXY_HOPS` và SSE đi qua proxy `/api` của Next.

---

## 2. Kết quả rà soát theo hạng mục

| Hạng mục | Kết quả |
|---|---|
| Frontend: build / TypeScript | `tsc` 0 lỗi; `next build` (Next 16.4.5) thành công ở chế độ thường và chế độ `/api` cùng origin. |
| Frontend: error boundary | **Thiếu → đã thêm** `app/error.tsx`, `app/(app)/error.tsx`, `app/global-error.tsx`, `app/not-found.tsx`. |
| Frontend: responsive | Đã kiểm ở 375 px (vòng 5): một cột, không cuộn ngang. Không phát hiện bề rộng cố định gây vỡ. |
| Frontend: promise không bắt lỗi | Các trang tự bắt lỗi; không thấy unhandled rejection. |
| Backend: CORS | Origin chính xác ở production. **Thiếu → đã thêm:** từ chối `*`, `null` và `http://` khi production. |
| Backend: header bảo mật | **Thiếu ở chính API → đã thêm** middleware: `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, HSTS khi production. Render/Railway không có reverse proxy nên trước đây API không có header nào. |
| Backend: SQL injection | Mọi SQL ghép chuỗi chỉ nằm trong `schema_migrations.py` với tên bảng/cột cố định. Không có đầu vào người dùng. |
| Backend: XSS | Không có `HTMLResponse` hay `dangerouslySetInnerHTML`. `widget.js` chỉ chèn cấu hình phía server. CSP dùng nonce + `strict-dynamic`. |
| Backend: CSRF | Header bắt buộc khi xác thực bằng cookie (đã kiểm: thiếu header → 403). |
| Backend: rate limiting | **Có lỗ → đã sửa:** người lạ khoá được tài khoản người khác; trên PaaS mọi người dùng chung một bucket IP. Xem 3.1. |
| Backend: xử lý lỗi | **Thiếu handler toàn cục → đã thêm** (500 trả JSON `{detail}`, không lộ chi tiết). **Lỗi upload lộ đường dẫn / lỗi thư viện → đã sửa.** |
| Backend: Auth/RBAC | Mọi endpoint đều có xác thực (user, admin, HMAC, chữ ký Stripe hoặc URL ký). Tài nguyên con được giới hạn theo cha. E2E xác nhận user đội khác nhận 404 khi đọc, sửa hay liệt kê ca. |
| Backend: biến môi trường production | **Có lỗ → đã sửa:** URL công khai mặc định là localhost; "production" được định nghĩa không thống nhất; `postgres://` làm sập lúc import. Xem 3.1. |
| Backend: tài liệu API | `/docs`, `/redoc`, `/openapi.json` công khai trên Caddy/Render/Railway → **đã tắt ở production** (chủ sản phẩm chọn). |
| CSDL: toàn vẹn schema | Mọi cột model đều có migration (1–6); `_validate_model_columns` chặn khởi động nếu thiếu. |
| CSDL: index | **Thiếu index trên truy vấn nóng → đã thêm migration 7** (xem 3.1). |
| CSDL: migration chưa áp dụng | Migration chạy tự động lúc API khởi động, trong khoá (SQLite `BEGIN IMMEDIATE` / PostgreSQL advisory lock), có sổ phiên bản. Bản này thêm phiên bản 7. |
| Kết nối E2E Client → API → DB | Đã chạy qua HTTP thật (mục 5). Cache Redis là tuỳ chọn: thiếu thì `/health/ready` báo `disabled`, không lỗi. |
| Docker / Compose | Xem 3.2. |
| Caddy / nginx | Upstream khớp tên service. SSE không đệm, không nén. Giới hạn body lớn hơn giới hạn backend. |
| CI/CD | **Có lỗ → đã sửa** workflow deploy (xem 3.2). |

---

## 3. Lỗi đã phát hiện và đã sửa — danh sách file

Mỗi lỗi có test hồi quy trong `tests/test_production_readiness.py` (26 test). Chạy trên `4d7068a`: **25 fail, 1 pass**. Test pass là test bảo vệ: cấu hình production hợp lệ vẫn khởi động được.

### 3.1 Backend và frontend

| File | Lý do sửa |
|---|---|
| `backend/security_headers.py` (mới), `backend/main.py` | Header bảo mật trên mọi phản hồi API; HSTS chỉ ở production. |
| `backend/main.py` | Handler lỗi toàn cục trả JSON không lộ chi tiết. Tắt `/docs`, `/redoc`, `/openapi.json` ở production. `/health/ready` gọi thử Ollama thật thay vì luôn báo "ok". `/metrics` dùng `is_production()`. |
| `backend/config.py` | `is_production()` dùng chung: `APP_ENV=prd/live` trước đây nhận khoá ký mặc định. Production từ chối khởi động khi `APP_PUBLIC_URL`/`API_PUBLIC_URL` không phải https công khai, hoặc khi `CORS_ORIGINS` có `*`, `null`, `http://`. Chuẩn hoá `postgres://` → `postgresql://`. Thêm `TRUSTED_PROXY_HOPS`. |
| `backend/cors.py`, `backend/auth/session_cookie.py` | Dùng `is_production()`. |
| `backend/auth/ratelimit.py`, `backend/auth/router.py` | Đăng nhập sai giờ đếm theo cặp (IP, email) với giới hạn 10/15 phút, cộng trần theo email 50/15 phút. Trước đây người lạ gõ sai 10 lần là khoá được tài khoản bất kỳ. IP thật lấy từ phần tử thứ N **tính từ phải** của `X-Forwarded-For` khi đặt `TRUSTED_PROXY_HOPS`, nên client không giả mạo được. |
| `backend/rag/errors.py` (mới), `backend/rag/ingest.py`, `backend/rag/ocr.py` | Lỗi upload chỉ trả nguyên văn những thông báo do FieldOps viết (`PublicIngestError`). Lỗi khác được ghi log, còn người dùng thấy thông báo chung (trước đây lộ đường dẫn `/app/data/...` và lỗi SQLite/thư viện). |
| `backend/schema_migrations.py`, `backend/chat/models.py`, `backend/costs/models.py`, `backend/field/models.py` | Migration 7 thêm index: `messages(created_at)` cho KPI và kiểm chất lượng mỗi giờ; `llm_usage_events(user_id, created_at)` vì ngân sách được kiểm ở mọi lượt AI trong khoá ghi; `field_service_cases(created_at)` và `(resolved_at)`. DB mới nhận index từ model, DB cũ nhận qua `CREATE INDEX IF NOT EXISTS`. |
| `backend/pipeline/jobs/tasks.py` | Backup thêm thư mục `uploads/` (tài liệu gốc, cần để lập chỉ mục lại). Cảnh báo khi `BACKUP_DIR` nằm cùng volume dữ liệu. |
| `frontend/components/ErrorScreen.tsx` (mới), `frontend/app/error.tsx`, `frontend/app/(app)/error.tsx`, `frontend/app/global-error.tsx`, `frontend/app/not-found.tsx` (mới) | Màn lỗi tiếng Việt có nút "Thử lại", "Tải lại trang", "Về Ca hiện trường". Trước đây chỉ có màn "Application error" trống trơn, nhất là khi chunk JS hỏng sau một lần deploy. Dữ liệu offline chưa gửi không bị ảnh hưởng. |

### 3.2 Triển khai

| File | Lý do sửa |
|---|---|
| `railway.toml` → `backend/railway.toml` | **Blocker trên Railway.** `dockerfilePath="backend/Dockerfile"` build với context là gốc repo, nên `COPY requirements.txt .` hỏng. Giờ đặt Root Directory = `backend`. Ghi chú liệt kê đủ biến bắt buộc. |
| `frontend/railway.toml` | Hướng dẫn `NEXT_PUBLIC_API_URL=/api` và `API_PROXY_TARGET`: `up.railway.app` là Public Suffix, nên nếu gọi API khác site thì cookie đăng nhập không được gửi. |
| `render.yaml` | **Mất dữ liệu:** web service free không có ổ đĩa, user/đội/ca nằm trên SQLite tạm. Giờ đặt `DATABASE_URL` từ Postgres. Các thay đổi khác: `API_PROXY_TARGET` thành `sync: false` (`fromService … host` là hostname mạng nội bộ nên mọi `/api/*` sẽ hỏng); thêm `APP_PUBLIC_URL`; thêm trần chi phí AI; thêm `TRUSTED_PROXY_HOPS`. |
| `deploy/cloud/docker-compose.yml`, `deploy/cloud/.env.example` | Trần chi phí AI mặc định $2/ngày và $0.20/user (chủ sản phẩm chọn; trước đây 0 = không giới hạn trong khi đăng ký mở công khai). Chuyển tiếp `LLM_PRICING_JSON`, `MAX_UPLOAD_MB`, `SMTP_USE_SSL`, `EMAIL_SENDER_NAME`: trước đây đặt trong `.env` cũng không có tác dụng. |
| `deploy/production/docker-compose.yml`, `docker-compose.gemini.yml` | Thêm service `rag-pipeline` (cùng image, volume và secret, read-only, không root): trước đây job KPI, retention, xoá ảnh hết hạn, backup và gửi webhook không bao giờ chạy trong stack này. `rag-backend` chờ `redis-rag` healthy. Thêm trần chi phí AI. |
| `deploy/cloud/deploy.sh`, `.github/workflows/deploy.yml` | Deploy đúng commit CI đã kiểm (`head_sha`), không phải `origin/main` lúc chạy. Chỉ deploy cho sự kiện push. Ghim `known_hosts` khi có secret `DEPLOY_KNOWN_HOSTS`. |
| `backend/Dockerfile`, `frontend/Dockerfile` | Thêm `HEALTHCHECK` cho cả hai. Image web chạy bằng user `node` thay vì root. |

### 3.3 Test được cập nhật

- `tests/test_schema_migrations.py`, `tests/test_postgres_migrations.py`: thêm migration 7 vào danh sách mong đợi.
- `tests/test_security_config.py`: cấu hình production hợp lệ giờ cần URL https công khai.
- `tests/test_document_updates.py::test_publication_lock_blocks_a_separate_process`:
  - Test này nhạy thời gian: tiến trình con import chromadb/langchain mất 6–9 s trên WSL. Nó fail 2/4 lần khi máy bận, nhưng pass 3/3 cả trên mã cũ lẫn mã mới khi chạy riêng file.
  - Đã nới hạn chờ **khởi động** 15 s → 60 s. Điều kiện được kiểm giữ nguyên: tiến trình con không lấy được khoá khi tiến trình cha đang giữ.

---

## 4. Vấn đề còn lại và cách xử lý

| Mức | Vấn đề | Cách xử lý |
|---|---|---|
| Cần kiểm trên deploy thật | `TRUSTED_PROXY_HOPS` trên Render/Railway. Số proxy phụ thuộc đường đi: edge của nền tảng, cộng proxy `/api` của Next. | Tạm ghi `request.headers["x-forwarded-for"]` ở một request thử (hoặc dùng log edge), đếm số địa chỉ do proxy nối thêm bên phải, rồi đặt N. Không đặt thì hành vi giữ như cũ (dùng IP kết nối). |
| Cần kiểm trên deploy thật | SSE (chat stream) đi qua rewrite của Next trên Render/Railway. | `curl -N https://<web>/api/chat/<id> …` và xác nhận token về dần, không dồn một lần ở cuối. |
| Trung bình | Image API vẫn chạy bằng root trên cloud compose và Render (stack production đã chạy `user: 1000:1000`). | Đổi sang non-root cần `chown` volume `fieldops_data` đã tồn tại. Thêm `USER 1000` và một bước `docker compose run --rm --user root api chown -R 1000:1000 /app/data` trong lần nâng cấp. Để làm thành một thay đổi riêng, có thử nâng cấp. |
| Trung bình | Bản sao lưu vẫn nằm trên cùng volume nếu không đặt `BACKUP_DIR`. | Đặt `BACKUP_DIR` vào một bind mount ổ khác, hoặc đồng bộ ra ngoài máy (rclone / Object Storage). Job backup giờ cảnh báo trong kết quả. |
| Thấp | Không tự rollback khi smoke test sau deploy thất bại. | Gắn tag image trước khi `up --build`; nếu `smoke.sh` lỗi thì `docker tag` lại bản trước và `up -d`. |
| Thấp | Trên Render free: Chroma, tài liệu tải lên và ảnh hiện trường vẫn nằm trên ổ tạm. | Gắn Render Disk (gói trả phí) ở `/app/data`, hoặc dùng VM + `deploy/cloud`. |
| Thấp | `next/font/google` cần mạng lúc build. | Build trong CI hoặc Docker có mạng (mặc định). |

---

## 5. Kiểm thử sanity và tích hợp (đã chạy)

| Bộ | Kết quả |
|---|---|
| Backend pytest (WSL, Python 3.12) | **606 passed, 12 skipped** (test cần PostgreSQL thật) |
| Test hồi quy vòng này, chạy trên `4d7068a` | **25 fail, 1 pass** (đúng kỳ vọng) |
| Frontend `tsc --noEmit` | 0 lỗi |
| `next build` thường / chế độ `/api` | thành công / thành công |
| Client tests (Node) | 23/23 |
| Eval offline: cổng BM25 / dev / holdout | đạt / pass 0.6964 / **đạt cổng**, pass 0.8333 |
| `docker compose config --quiet` (với `.env.example` + biến giả) | cloud ✅, production ✅, production + gemini ✅ |
| **E2E luồng chính** (HTTP thật, DB tạm) | **17/17**. Gồm: đăng ký và đăng nhập bằng cookie; CSRF; tạo đội và mời thành viên; tạo ca; phân quyền điều phối; gửi lại thao tác offline đúng 1 lần; lập lịch (25 ms) với ca khẩn lên đầu; áp dụng lịch; webhook CRM có chữ ký và bị từ chối khi chữ ký sai; trợ lý trả 503 JSON khi truy hồi lỗi; gói cước; đăng xuất. |
| **E2E luồng biên** | **16/16**. Gồm: header bảo mật trên API; 404 JSON; IDOR khi đọc, sửa hay liệt kê ca của đội khác → 404; dữ liệu sai → 422; upload định dạng lạ hoặc file rỗng → 400; upload >20 MB → 413; webhook gửi lại không tạo ca trùng; timestamp cũ → 401; đăng xuất thu hồi token; đăng nhập sai liên tục → 429. |
| **Khởi động chế độ production** | Thiếu URL công khai → **từ chối khởi động** với thông báo rõ. Cấu hình đúng thì: `/docs`, `/openapi.json`, `/redoc` → 404; `/metrics` không token → 401; có HSTS cùng các header bảo mật. `/health/ready` → 503 `llm: missing_key` (đúng, vì môi trường thử không có khoá Gemini). |

**Kết luận:** sẵn sàng cho dry run trên máy local. Trước khi deploy thật, cần làm checklist ở mục 6.

---

## 6. Checklist cuối trước khi deploy

### A. VM + Caddy (`deploy/cloud`) — khuyến nghị

1. `cp deploy/cloud/.env.example deploy/cloud/.env`, rồi điền:
   - `APP_DOMAIN`, `ACME_EMAIL`
   - `SECRET_KEY` (`openssl rand -hex 32`), `METRICS_TOKEN` (`openssl rand -hex 24`)
   - `GEMINI_API_KEY`
   - xem lại `LLM_DAILY_BUDGET_USD=2` và `LLM_USER_DAILY_BUDGET_USD=0.2`
2. DNS của `APP_DOMAIN` trỏ về VM; mở cổng 80 và 443.
3. Kiểm tra cấu hình mà không in giá trị:
   ```bash
   cd deploy/cloud && docker compose --env-file .env config --quiet && echo OK
   ```
4. Build và chạy:
   ```bash
   docker compose --env-file .env up -d --build
   ```
5. Kiểm tra sau khi chạy:
   ```bash
   bash smoke.sh "https://$APP_DOMAIN"
   ```
   - Mong đợi: web 200, health 200, ready 200, metrics không token 401.
6. `docker compose ps`: `api` và `pipeline` đều healthy hoặc running; log của `pipeline` có lượt chạy lịch.
7. Đặt `BACKUP_DIR` ra ổ khác hoặc đồng bộ ra ngoài máy.
8. Trên GitHub: secrets `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, và `DEPLOY_KNOWN_HOSTS` (`ssh-keyscan -p 22 <host>`).

### B. Stack production (`deploy/production`, nginx + secrets)

1. Chạy `python bootstrap_local.py`. Lệnh này sinh secrets, `KAFKA_CLUSTER_ID` và TLS local.
2. Kiểm tra cấu hình:
   ```bash
   docker compose --env-file .env -f docker-compose.yml -f docker-compose.gemini.yml config --quiet
   ```
   - Nếu không dùng overlay gemini, phải có service Ollama thật: `/health/ready` giờ báo `llm: unreachable` khi thiếu.
3. Build và chạy:
   ```bash
   docker compose --env-file .env -f docker-compose.yml -f docker-compose.gemini.yml up -d --build
   ```
   rồi chạy `python smoke_production.py`.
4. Kiểm tra `rag-pipeline` đang chạy.

### C. Render

1. Tạo từ blueprint `render.yaml`.
2. **Trước lần build đầu của `rag-web`**, đặt `API_PROXY_TARGET=https://<rag-api>.onrender.com`.
3. Trên `rag-api`, đặt:
   - `APP_PUBLIC_URL=https://<rag-web>.onrender.com`
   - `API_PUBLIC_URL=https://<rag-web>.onrender.com/api`
   - `CORS_ORIGINS=https://<rag-web>.onrender.com`
   - `GEMINI_API_KEY`
4. Deploy lại `rag-web` (rewrites được tính lúc build).
5. Kiểm tra: đăng nhập không quay vòng về /login; `https://<rag-web>/api/health` trả 200; chat stream hiện dần từng phần.
6. Xác định rồi đặt `TRUSTED_PROXY_HOPS` (mục 4).

### D. Railway

1. Service API: Root Directory = `backend`. Đặt các biến liệt kê trong `backend/railway.toml`, gồm `DATABASE_URL=${{Postgres.DATABASE_URL}}`.
2. Service web: Root Directory = `frontend`. Đặt `NEXT_PUBLIC_API_URL=/api` và `API_PROXY_TARGET=https://<api>.up.railway.app`.

### E. Chung

- `APP_ENV=production` ở mọi nơi. API sẽ tự từ chối khởi động nếu khoá ký yếu, URL công khai là localhost hoặc http, hay CORS có `*`.
- Sau lần khởi động đầu, xem log có dòng migration và xác nhận bảng `schema_migrations` có phiên bản 7.
- Chạy backup thủ công một lần: `python -m pipeline.jobs.runner --only backup`. Kết quả phải có `app-`/`chroma-`/`uploads-`, hoặc có ghi chú dùng backup của nhà cung cấp Postgres.
