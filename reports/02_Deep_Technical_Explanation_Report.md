# Báo cáo giải thích kỹ thuật chuyên sâu — FieldOps

**Mốc mã nguồn:** HEAD `7569b27` cộng các bản sửa của hai vòng kiểm toán 2026-11-16 (chưa commit).

Báo cáo này mô tả các luồng, ranh giới tin cậy và thuật toán **xuyên suốt** hệ thống. Chi tiết từng phần nằm trong [`he-thong/`](he-thong/README.md). Mọi khẳng định đều kèm vị trí trong code; phần nào chưa được chạy thực tế được ghi rõ ở §6.

---

## 1. Bài toán

FieldOps phục vụ đội kỹ thuật hiện trường (FTTH, BTS, thiết bị mạng) với bốn chức năng:

1. **Ca sửa:** checklist, nhật ký, ảnh, phân quyền theo đội.
2. **Điều phối tuyến:** VRP có khung giờ, giải bằng heuristic.
3. **Trợ lý tra cứu SOP và manual có dẫn chứng:** mỗi nhãn `【E#】` trỏ tới một đoạn trích có SHA-256, và nhãn được kiểm **ngay trong lúc stream**.
4. **Tích hợp CRM/NMS:** webhook vào có ký HMAC; webhook ra có outbox, claim nguyên tử, ghim IP và retry.

Có bốn loại client:

| Client | Đặc điểm |
|---|---|
| Web (Next.js) | Có CSP; outbox IndexedDB |
| Mobile (Expo) | Outbox SecureStore, gắn với đúng máy chủ đã tạo ra dữ liệu |
| Widget nhúng | Chạy trên website khách |
| Public API | Dùng API key |

---

## 2. Các luồng request

### 2.1 Xác thực (chi tiết: hệ thống 1)

**Đăng ký và đăng nhập bằng mật khẩu**

- bcrypt, có chặn đầu vào trên 72 byte.
- Email so sánh **không phân biệt hoa/thường**: kiểm trùng khi đăng ký; khi đăng nhập, ưu tiên khớp chính xác, rồi chỉ nhận khớp không phân biệt hoa/thường nếu có đúng một tài khoản.

**JWT**

- HS256 (allow-list `HS256`/`HS384`/`HS512`, kiểm lúc khởi động), có hiệu lực 7 ngày.
- Claim: `sub`, `exp`, `purpose="access"`, `version=token_version`.
- Mỗi request đối chiếu `token_version` và `deleted_at` trong DB.

**Đăng nhập Google**

- Xác minh JWKS, `aud`, `iss` và `email_verified`.
- Liên kết vào một tài khoản mật khẩu **chưa xác minh** thì vô hiệu mật khẩu cũ và tăng `token_version` (chống pre-hijack).

**Phiên web bằng cookie httpOnly (vòng 3)**

- Web gửi `X-Auth-Mode: cookie`. Server đặt JWT vào cookie httpOnly (SameSite=Lax) và không trả token trong body.
- Request ghi dùng cookie phải có `X-Requested-With: fieldops` (chống CSRF).
- Mobile và Public API giữ Bearer.

**Quên mật khẩu**

- Email gửi ở nền, nên thời gian phản hồi không lộ email nào tồn tại.
- Limiter khoá theo `IP|email`.

### 2.2 Chat RAG qua SSE (chi tiết: hệ thống 3)

```mermaid
sequenceDiagram
  participant C as Client
  participant R as chat.router
  participant Q as quota/budget
  participant RT as retriever
  participant L as chain + llm
  C->>R: POST /chat/{conv} {question ≤ 4000}
  R->>Q: enforce_budget, enforce_query_quota (UPDATE nguyên tử, commit)
  R->>R: lưu câu hỏi; lịch sử ≤ 10 lượt × 2000 ký tự, bỏ nhãn cũ
  L->>RT: vector ∥ BM25L → RRF → rerank (cache theo revision)
  L-->>R: evidence bundle, context (【】 trong tài liệu đã vô hiệu)
  loop mỗi token
    L->>L: giữ phần từ ngoặc 【 chưa đóng; audit nhãn đã đóng
    alt nhãn hợp lệ / không có nhãn
      L-->>C: token
    else nhãn không tồn tại
      L-->>C: error (nhãn bịa không bao giờ được gửi)
    end
  end
  L->>L: audit cuối → lưu answer + evidence; judge (p=0,1) nếu có context
  R-->>C: done {sources, evidence, citation_audit}
```

### 2.3 Ingest tài liệu

Thứ tự xử lý:

1. Kiểm tra quyền, tên tệp, phần mở rộng và kích thước 20 MB.
2. **Ngân sách AI.**
3. Loader hoặc OCR.
4. Splitter (1000/100).
5. Embedding theo lô 20.
6. Journal, rồi publish vào Chroma, rồi tăng revision.
7. Medallion (tuỳ chọn): bronze → GE → silver → gold. Gold **luôn** được đồng bộ lại; ngày được tính theo UTC.

### 2.4 Ca sửa, offline và phân quyền (chi tiết: hệ thống 2, 6, 7)

**Truy cập ca**

- Mọi truy cập đều join membership; người ngoài đội nhận 404.

**Phân quyền**

- Owner: sửa mọi thứ.
- Kỹ thuật viên: chỉ nhận ca chưa giao cho mình, và chỉ đổi trạng thái ca của mình.
- `PATCH /dispatch`: chỉ owner.

**Outbox ở client**

- Lưu trước, gửi sau.
- `client_action_id` được dùng làm khoá idempotency.
- Lỗi 5xx quá 8 lần thì thao tác chuyển sang `rejected`.

**Server**

- `request_hash = SHA-256({body, target})`.
- Replay một mã đã dùng: trả kết quả cũ, hoặc 409 nếu khác nội dung.
- Race giữa hai lần gửi lại đồng thời: trả replay.

### 2.5 Tích hợp CRM/NMS

**Chiều vào**

1. Kiểm `Content-Length` và đọc body theo stream, tối đa 256 KB.
2. Kiểm chữ ký HMAC (lệch đồng hồ tối đa 300 s).
3. Dedupe theo `event_id`.
4. Race khi tạo ca theo `external_ref`: trả 503 kèm `Retry-After`.

**Chiều ra**

1. Ghi outbox cùng transaction với thay đổi của ca.
2. Claim bằng `UPDATE` có điều kiện (lease 5 phút).
3. `validate_outbound_url` (chỉ IP `is_global`), rồi POST tới đúng IP đã kiểm (`Host` và SNI giữ tên miền).
4. Backoff; quá 8 lần thì `dead`. Gauge `fieldops_field_webhook_deliveries` cấp dữ liệu cho alert.

### 2.6 Billing

**Checkout**

- `Customer.create` có idempotency key.
- `UPDATE … WHERE stripe_customer_id IS NULL`, rồi đọc lại customer đã lưu.

**Webhook**

- Chữ ký Stripe.
- Tìm tài khoản theo `customer`.
- `billing_sync_version` đơn điệu.

**Vô hiệu hoá tài khoản:** huỷ các subscription còn hiệu lực.

### 2.7 Job vận hành

- `run_chain` chạy dưới khoá file dùng chung giữa API và container pipeline.
- Khi khởi động, các lượt `running` cũ được đánh dấu `fail`.
- Chạy bù dựa trên mốc `backup`.
- Mỗi chuỗi được bọc trong `_safe_chain`, nên lỗi tạm thời không làm dừng vòng lặp lịch.

---

## 3. Ranh giới tin cậy

| Ranh giới | Thứ được tin | Cơ chế | Còn hở |
|---|---|---|---|
| Internet → edge | Không gì | TLS, HSTS, body ≤ 30 MB. XFO SAMEORIGIN, riêng `/embed` có `frame-ancestors *`. Edge ghi đè `X-Forwarded-For`. Permissions-Policy `camera=(self), geolocation=(self)`. | Hai cấu hình viết tay; được giữ khớp bằng `tests/test_edge_config_parity.py` |
| Trình duyệt → console | Script mang nonce của request | CSP có nonce và `'strict-dynamic'` (`proxy.ts`): HTML chèn vào không chạy được script; `connect-src` chỉ tới API đã cấu hình; `object-src 'none'`; `frame-ancestors 'self'` | `style-src` vẫn có `'unsafe-inline'` (CSS không thực thi mã) |
| Cookie phiên web | Chữ ký, `token_version`, và header CSRF với phương thức ghi | httpOnly, nên JS (kể cả XSS) không đọc được token | Nếu API và web khác site thì cần `AUTH_COOKIE_SAMESITE=none` cộng CORS |
| Bearer JWT (mobile, API) | Chữ ký và `token_version` trong DB | §2.1 | — |
| Google ID token | JWKS, `aud`, `iss`, email đã xác minh | Chống pre-hijack | — |
| API key | SHA-256 của key | Phạm vi theo user | — |
| Embed token | **Công khai** | `allowed_origins` so với origin của trang cha (`ancestorOrigins`/`referrer`) | Client không phải trình duyệt giả được header |
| Vai trò trong workspace | Membership trong DB | Owner và kỹ thuật viên (§2.4) | — |
| CRM/NMS → API | Secret HMAC của từng tích hợp | §2.5 | — |
| API → URL webhook của owner | Không tin | https, IP `is_global`, ghim IP, không theo redirect | Dev có `FIELD_WEBHOOK_ALLOW_PRIVATE` thì tắt các kiểm tra này |
| Tài liệu → prompt | Là dữ liệu | Vô hiệu `【】`, `［］` và `---`; quy tắc đi qua kênh system riêng (Gemini `system_instruction`, Ollama `system`) | Mô hình vẫn có thể bị dẫn dắt; audit nhãn là lớp chặn cuối |
| LLM → người dùng | Không tin | Audit nhãn **trong lúc stream**; nhãn sai thì dừng và không lưu | Chỉ kiểm danh tính nhãn, không kiểm nội dung đúng nghĩa |
| Ảnh upload | Không tin | Nhận dạng bằng nội dung tệp; chặn > 40 MP trước khi giải nén; mã hoá lại bỏ EXIF/GPS; kiểm hạn mức lại dưới khoá ghi | — |
| Thiết bị mobile → máy chủ | Origin đã gắn cho dữ liệu offline | Chỉ gắn khi ghi; đọc chỉ kiểm tra | Máy đã bị khoá nhầm ở bản cũ |

---

## 4. Thuật toán: công thức và ví dụ tính

| Thuật toán | Công thức và tham số (vị trí) | Ví dụ |
|---|---|---|
| Chia đoạn | $N \approx \lceil (L-O)/(S-O)\rceil$, S = 1000, O = 100 (`rag/ingest.py`) | L = 2500 cho **3** chunk |
| BM25L | $idf=\ln(N+1)-\ln(df+0{,}5)$; $c=\frac{tf}{1-b+b\,|d|/\overline{|d|}}$; $s=\sum idf\cdot tf\cdot\frac{(k_1+1)(c+\delta)}{k_1+c+\delta}$, với k₁ = 1,5, b = 0,75, δ = 0,5 (`rag/bm25.py`) | N = 10, df = 2, tf = 2, $|d|$ bằng trung bình: **≈ 4,63** |
| RRF | $\sum 1/(60+r_i)$, hạng tính từ 1 | Hạng (1, 3) cho 0,032266, cao hơn hạng (2) với 0,016129 |
| Rerank lexical | $4|Q\cap H|+2|Q\cap B|+|C\cap H|$ (`rag/rerank.py`) | Từ "pon" ở tiêu đề, "pon" và "đỏ" ở thân: **8** |
| Giữ nhãn khi stream | Phát phần văn bản tới trước ngoặc mở chưa đóng; giữ tối đa 64 ký tự, không qua xuống dòng; audit phần đã phát trước mỗi lần phát (`rag/chain.py:_safe_release_point`) | `"Đúng 【E9"` chỉ phát `"Đúng "`; `"99】"` đóng nhãn E999, audit báo `invalid`, stream dừng |
| Quota | $used'=used+1$ khi $used<limit$; Free 50, Pro 1000 câu/ngày UTC | — |
| Trần ngân sách cứng | Chấp nhận khi $\text{đã\_chi}+\sum\text{đặt\_chỗ còn hạn (gồm } r) \le \text{trần}$, kiểm trong giao dịch có khoá; trả đặt chỗ khi request xong; hết hạn sau 15 phút (`costs/budget.py`). $r$ = cost(`LLM_MAX_INPUT_TOKENS`, `LLM_MAX_OUTPUT_TOKENS`) (+ Cohere), và `llm.py` ép đúng hai trần đó, nên một request không vượt được $r$ | Trần 0,015, $r$ = 0,01: A nhận; B (khi A đang chạy) cần 0,02 nên bị từ chối; A xong với chi phí 0,003 thì B nhận (0,013) |
| Chi phí | $\frac{in\cdot p_{in}+(out+thinking)\cdot p_{out}}{10^6}+units\cdot p_{unit}$; ước lượng $\lceil len/4\rceil$ khi thiếu số token | 3000 token vào, 400 ra, flash: **0,0019 USD** |
| Lập tuyến | $travel=\lceil 1{,}35\,d/v\cdot60\rceil$; $cost=\sum(travel+w(start-shift))$, w = {0,5; 0,2; 0,05; 0}; chèn rẻ nhất với điểm $\Delta cost+3|seq|$, rồi 2-opt | 4 km cho **11 phút**; ca khẩn chờ 1 giờ "đắt" bằng 30 phút lái xe |
| Retry webhook | $\min(30\cdot2^a, 21600)$ s; lease claim 5 phút; tối đa 8 lần | a = 3 cho 240 s |
| Outbox client | Lỗi 5xx: `failures += 1`; chuyển sang `rejected` khi `failures ≥ 8`; lỗi mạng không đếm | — |
| Judge | Bernoulli p = 0,1 trên câu trả lời có context; điểm clamp vào [0, 1] | n = 1000 cho 100 ± 9,5 câu được chấm |
| nDCG@k | $\frac{\sum rel_i/\log_2(i+1)}{\sum_{i\le\min(|rel|,k)}1/\log_2(i+1)}$ | Nguồn đúng ở hạng 1 và 3: **0,9197** |
| Grader | Một cụm bị phủ định bởi "không …" ngay phía trước thì **không** khớp; câu có `【E#】` không phải lời từ chối | "Không được tắt nguồn" không chứa dữ kiện "tắt nguồn" |
| Ngày UTC | `CAST(ts AT TIME ZONE 'UTC' AS DATE)`; `timezone('UTC', ts)` | Nạp lúc 23:30 UTC ngày 15 vẫn tính cho ngày 15 dù máy chủ ở UTC+7 |

---

## 5. Lựa chọn công nghệ và giới hạn

| Công nghệ | Lý do chọn | Giới hạn |
|---|---|---|
| FastAPI + SQLAlchemy 2 | Async và SSE; migration có ledger, chạy trên SQLite và PostgreSQL | SQLite chỉ cho một writer; các khoá ghi trong code (ảnh, vô hiệu hoá tài khoản) dùng `UPDATE` không đổi gì để lấy khoá |
| Chroma + rank_bm25 | Không cần server riêng; bắt đúng mã lỗi chính xác | Index BM25 nằm trong RAM; phải dựng lại khi revision đổi |
| Gemini / Ollama | Chất lượng và OCR / chạy cục bộ | Quy tắc đi qua `system_instruction` / `system`; OCR luôn cần Gemini |
| Stripe | Checkout, Portal, webhook | Cần idempotency key (đã thêm) |
| Pillow | Kiểm tra, mã hoá lại, bỏ metadata | Chỉ tự raise ở 2× giới hạn, nên phải tự chặn số pixel (đã thêm) |
| Next.js 16 | App Router; CSP có nonce qua `proxy.ts` | Mọi trang render theo từng request (đánh đổi một chút hiệu năng lấy nonce) |
| Expo SecureStore | Lưu trữ được mã hoá | Giới hạn kích thước mỗi khoá nên phải chia chunk; không có transaction nên phải tự khoá |
| Caddy / nginx | TLS tự động / cấu hình chi tiết | Hai biến thể phải đồng bộ |
| Prometheus | Metric và alert | Metric của tiến trình không được scrape thì phải xuất qua API (đã thêm gauge từ DB) |
| Khoá file (`storage_lock`) | Dùng chung qua volume data, chạy trên cả Windows và POSIX | Chỉ hiệu lực khi các tiến trình dùng chung volume |

---

## 6. Trạng thái kiểm chứng

| Thành phần | Cách kiểm | Kết quả |
|---|---|---|
| Backend (mọi hệ thống) | pytest trên WSL | **537 passed, 12 skipped** sau vòng 4 (baseline 468/12) |
| Các bản sửa backend | 58 regression test (16 + 32 + 10), mỗi vòng chạy trên mã trước vòng đó | Đều FAIL đúng lỗi, PASS sau khi sửa; trừ 1 test chống hồi quy pass ở cả hai |
| Web và mobile | `node --test` | 20/20; các test mới của từng vòng FAIL trên mã trước vòng đó |
| Typecheck, build | `npm ci`, `tsc`, `next build` | PASS; mọi trang render theo từng request; nhận diện Proxy |
| End-to-end web | API WSL (DB tạm), `next start`, built-in browser | Đăng ký, đăng nhập, reload, tạo hội thoại, đăng xuất qua cookie httpOnly; token cũ tự đổi sang cookie; HTML chèn vào bị CSP chặn |
| Eval offline | 3 script của CI | Giống hệt trước khi sửa |
| Compose | `docker compose config` | PASS; thiếu `ACME_EMAIL` thì báo lỗi rõ ràng |
| PostgreSQL, Caddy, promtool, thiết bị mobile, provider thật, **eval live** | — | **Chưa chạy** (giới hạn môi trường; CI chạy PostgreSQL và promtool; eval live cần `GEMINI_API_KEY` trong `.env`) |

**Tái lập trên Windows**

1. **Backend:** chạy trên WSL, vì `xxhash` bị Application Control chặn trên Windows. Cài thư viện bằng `PYTHONUTF8=1 pip install -r backend/requirements.txt`. Khi chạy pytest, dùng `--basetemp` với một đường dẫn ngắn.
2. **Web và mobile:** chạy `npm ci` trước.
