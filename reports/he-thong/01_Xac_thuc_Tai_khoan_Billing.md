# Hệ thống 1: Xác thực, tài khoản, billing và các cổng truy cập

**Phạm vi mã nguồn:**

- `backend/auth/`
- `backend/dependencies.py`
- `backend/config.py` (phần JWT và các kiểm tra lúc khởi động)
- `backend/billing/router.py`
- `backend/embed/router.py`
- `backend/public_api/router.py`
- `backend/admin/router.py`
- `backend/cors.py`
- `backend/saas/security.py`

**Mốc mã nguồn:** HEAD `7569b27` cộng các bản sửa của đợt kiểm toán 2026-11-16 (chưa commit).

---

## 1. Vai trò của hệ thống

Đây là "cửa ra vào" của mọi client. Hệ thống trả lời hai câu hỏi cho mỗi request:

1. **Ai đang gọi?** Có năm loại danh tính:

   | Danh tính | Dùng cho |
   |---|---|
   | JWT Bearer | Web và mobile |
   | Đăng nhập Google | Web và mobile |
   | API key | `/v1/*` |
   | Embed token kèm guest session | Widget nhúng trên website khách |
   | Chữ ký HMAC | CRM/NMS gửi sự kiện vào (xem Hệ thống 2) |

2. **Người đó còn quyền không?** Code kiểm tra `token_version`, `deleted_at`, `role` và gói cước.

Billing quyết định gói (`free` hoặc `pro`), và gói quyết định quota câu hỏi mỗi ngày (xem Hệ thống 3).

---

## 2. Luồng xử lý

### 2.1 Đăng ký và đăng nhập bằng mật khẩu

**Đăng ký: `POST /auth/register` → `register_user`**

1. Email được kiểm tra trùng **không phân biệt hoa/thường** (`lower(email)`).
2. Mật khẩu được băm bằng bcrypt, có chặn đầu vào trên 72 byte.
3. Tài khoản `id == 1` thành admin. Việc xét này nằm sau `flush()`, nên hai lượt đăng ký đồng thời trên DB trống không thể cùng thành admin.

**Đăng nhập: `POST /auth/login` → `authenticate_user` → `find_active_user_by_email`**

1. Ưu tiên khớp email chính xác.
2. Nếu không có, chỉ chấp nhận khớp không phân biệt hoa/thường khi kết quả là **đúng một** tài khoản.
3. Rate-limit: 10 lần trên mỗi email và 30 lần trên mỗi IP, trong 15 phút. Đăng nhập thành công thì bộ đếm theo email được xoá.

**JWT (HS256, có hiệu lực 7 ngày):**

```json
{"sub": "<id>", "exp": "...", "purpose": "access", "version": "<token_version>"}
```

**Mỗi request:** `dependencies.get_current_user` làm các bước sau.

1. Giải mã JWT với thuật toán ghim cố định; bắt buộc có `exp` và `sub`.
2. Kiểm tra `purpose == "access"`.
3. Truy vấn user có `deleted_at IS NULL` và `token_version` khớp với claim.

Đổi mật khẩu, đặt lại mật khẩu, vô hiệu hoá tài khoản hay liên kết Google vào một tài khoản chưa xác minh đều tăng `token_version`, nên mọi token cũ mất hiệu lực ngay.

### 2.2 Đăng nhập Google (`auth/google.py`, `login_with_google`)

**Bước 1: xác minh ID token**

- Chữ ký RS256 qua JWKS.
- `aud` phải thuộc `GOOGLE_CLIENT_ID`, và `iss` phải đúng.
- Kiểm tra `exp` và `iat`.
- Bắt buộc `email_verified = true`.

**Bước 2: tìm hoặc tạo tài khoản theo thứ tự**

1. Tìm theo `google_sub`.
2. Không có thì tìm theo `lower(email)`:
   - Nếu email của tài khoản đó **đã được xác minh**: liên kết, và giữ nguyên mật khẩu.
   - Nếu **chưa xác minh** (đã sửa, D1): đặt mật khẩu thành không dùng được, tăng `token_version`, đánh dấu đã xác minh, rồi mới liên kết. Người đã đăng ký trước bằng email của nạn nhân mất toàn bộ quyền truy cập. Chủ thật có thể đặt mật khẩu mới qua "Quên mật khẩu".
3. Vẫn không có thì tạo tài khoản mới với `auth_provider = "google"`.

### 2.3 Quên và đặt lại mật khẩu

- **Rate-limit (đã sửa):** khoá theo cặp `IP|email` với 5 lần mỗi giờ, cộng thêm 20 lần mỗi giờ trên mỗi IP. Trước đây khoá chỉ theo email, nên bất kỳ ai cũng chặn được email đặt lại mật khẩu của người khác.
- **Gửi email ở nền (đã sửa):** phản hồi luôn giống nhau và không chờ SMTP. Thời gian phản hồi không còn tiết lộ email nào tồn tại.
- **Token đặt lại mật khẩu:**
  - dùng một lần, nhờ `UPDATE` có điều kiện;
  - gắn với hạn dùng trong DB và với `token_version`;
  - commit cùng transaction với việc đổi mật khẩu.

### 2.4 Billing qua Stripe (`billing/router.py`)

**Checkout (đã sửa):**

1. `Customer.create(idempotency_key="fieldops-customer-{id}")`.
2. Ghi customer bằng `UPDATE … WHERE stripe_customer_id IS NULL`.
3. Đọc lại customer đã được lưu.
4. Tạo Checkout Session.

Hai lần bấm đồng thời luôn dùng chung một customer.

**Webhook:**

1. Xác minh chữ ký trên raw body.
2. Tìm tài khoản theo `customer`, không bao giờ theo metadata.
3. Đọc lại subscription trực tiếp từ Stripe.
4. Ghi gói với `billing_sync_version` đơn điệu, nên một kết quả đọc cũ không ghi đè được kết quả mới hơn.

**Vô hiệu hoá tài khoản (đã sửa):** sau khi commit, code gọi `cancel_customer_subscriptions`, huỷ mọi subscription ở trạng thái `active`, `trialing` hoặc `past_due`. Nếu Stripe lỗi, việc vô hiệu hoá vẫn giữ nguyên và một cảnh báo được ghi vào log để xử lý thủ công.

### 2.5 Widget nhúng (`embed/router.py`, `frontend/app/embed/page.tsx`)

1. `widget.js` chèn một iframe tới `APP_PUBLIC_URL/embed?token=…`.
2. Trang embed gọi `/embed/chat` với hai header:
   - `X-Embed-Token`;
   - `X-Embed-Parent-Origin` (mới): origin của trang cha, lấy từ `location.ancestorOrigins` (do trình duyệt điền, trang cha không giả được), hoặc từ `document.referrer`.
3. `_check_origin` (đã sửa) chấp nhận request khi `allowed_origins` của token rỗng, hoặc khi thoả một trong hai điều kiện:
   - `Origin` thuộc `allowed_origins` (website khách gọi API trực tiếp);
   - `Origin` là origin của chính ứng dụng **và** origin trang cha thuộc `allowed_origins` (widget chuẩn).

   Trước đây chỉ có điều kiện thứ nhất. Vì request đi từ iframe nên `Origin` luôn là origin của ứng dụng, và một token đã cấu hình `allowed_origins` thì luôn bị 403.
4. Guest session gắn với token, hội thoại, chủ token và hạn dùng. Quota và ngân sách được tính cho chủ token.

### 2.6 Public API và admin

- **API key:** tra theo SHA-256 của key, phạm vi theo `user_id`, loại các user đã bị xoá.
- **`/v1/ask`:** dùng chung quota, ngân sách và lõi RAG với chat. Trừ quota khi provider lỗi là **chủ ý** (D4, giữ nguyên).
- **Admin:** `require_admin` đọc role từ DB ở mỗi request. Admin không tự vô hiệu hoá được chính mình.

### 2.7 Kiểm tra cấu hình lúc khởi động (`config.py`, đã sửa)

- `ALGORITHM` phải thuộc `{HS256, HS384, HS512}`. Giá trị `none` hay `RS256` dùng với khoá đối xứng làm tiến trình dừng ngay.
- `0 ≤ CHUNK_OVERLAP < CHUNK_SIZE`.
- Production hoặc staging bắt buộc có `SECRET_KEY` riêng, dài ít nhất 32 ký tự.

---

## 3. Ranh giới tin cậy

| Đầu vào | Được tin khi | Cơ chế |
|---|---|---|
| JWT | Chữ ký đúng **và** `token_version` khớp DB | §2.1 |
| Google ID token | Đúng JWKS, `aud`, `iss`, và email đã xác minh | §2.2 |
| API key | Hash tồn tại và chủ key chưa bị xoá | §2.6 |
| Embed token | Là **công khai** (nằm trong mã nguồn trang khách) | Chỉ cấp quyền hỏi; origin kiểm theo §2.5 |
| Stripe webhook | Chữ ký `Stripe-Signature` | §2.4 |
| `X-Forwarded-For` | **Không tin** | Rate-limit dùng `request.client.host`; uvicorn ghim `forwarded-allow-ips` vào gateway |

---

## 4. Lỗi đã sửa

| ID | Mức | Vị trí | Kịch bản lỗi trước khi sửa | Cách sửa | Regression test |
|---|---|---|---|---|---|
| D1 | **Cao** | `auth/service.py:login_with_google` | Kẻ tấn công đăng ký trước `victim@gmail.com` bằng mật khẩu của mình. Khi nạn nhân đăng nhập Google, tài khoản được gộp và đánh dấu đã xác minh, nhưng mật khẩu và JWT của kẻ tấn công vẫn dùng được. | Liên kết vào tài khoản chưa xác minh thì vô hiệu mật khẩu và tăng `token_version`. | `round2::test_google_link_into_unverified_account_revokes_the_pre_registered_session`; `test_auth_providers::test_google_link_keeps_password_of_already_verified_account` |
| F4 | TB | `auth/service.py:register_user` | `Alice@x.com` và `alice@x.com` thành hai tài khoản, và Google liên kết vào một trong hai một cách tuỳ ý. | So trùng theo `lower(email)`. | `test_registration_rejects_case_variant_of_existing_email` |
| — | TB | `authenticate_user`, `field/router.py:add_member` | Email lưu dạng `Tech@Example.com` thì không đăng nhập được bằng `tech@example.com`, và trưởng đội không thêm được người này vào đội (code so chuỗi đã chuyển thường với cột gốc). | `find_active_user_by_email`. | `round2::test_login_and_team_invite_find_mixed_case_stored_email` |
| U12 | Thấp | `auth/router.py:forgot_password` | Phản hồi chậm hơn với email có thật vì chờ SMTP. Năm request từ một IP chặn được email đặt lại mật khẩu của nạn nhân. | Gửi nền; limiter khoá theo `IP|email`. | `round2::test_forgot_password_limit_is_per_requester_and_does_not_wait_for_smtp` |
| F3 | TB | `billing/router.py:create_checkout` | Hai lần checkout đồng thời tạo hai customer. Người dùng trả tiền trên customer bị ghi đè, webhook không tìm ra tài khoản, và gói không bao giờ được nâng. | Idempotency key, `UPDATE` có điều kiện, đọc lại customer đã lưu. | `test_concurrent_checkout_reuses_the_stored_customer` |
| U13 | Thấp | `auth/lifecycle.py:deactivate_account` | Tài khoản bị vô hiệu hoá nhưng Stripe vẫn trừ tiền hằng tháng. | Huỷ subscription sau khi commit. | `round2::test_deactivating_an_account_cancels_its_stripe_subscription` |
| U2 | TB | `embed/router.py:_check_origin`, `embed/page.tsx` | Đặt `allowed_origins` làm widget luôn bị 403. Muốn chạy được thì phải cho phép origin của ứng dụng, mà như vậy thì không còn bảo vệ gì. | So origin của trang cha (`X-Embed-Parent-Origin`). | `round2::test_widget_origin_is_checked_against_the_framing_site` |
| U16b | Thấp | `frontend/app/embed/page.tsx` | Link `/embed?token=…&api=https://evil` gửi câu hỏi của khách tới máy chủ tuỳ ý. | Bản build đã cấu hình `NEXT_PUBLIC_API_URL` thì bỏ qua `?api=`; CSP `connect-src` chặn thêm một lớp. | Kiểm tra trên trình duyệt (§7) |
| U19 | Thấp | `config.py` | `ALGORITHM=none` hoặc `CHUNK_OVERLAP ≥ CHUNK_SIZE` chỉ lộ ra lúc xác thực hoặc lúc ingest. | Kiểm tra khi import `config`. | `round2::test_invalid_signing_or_chunking_config_fails_at_startup` |
| F11 | Thấp–TB | `auth/lifecycle.py` | Người bị vô hiệu hoá vẫn là `default_assignee` của tích hợp CRM, nên ca mới được giao cho người ngoài đội. | Đặt `default_assignee = NULL`. | `test_deactivated_member_is_removed_as_integration_default_assignee` |

Tiền tố `round2::` là tệp `tests/test_audit_regressions_round2.py`. Các tên không có tiền tố nằm trong `tests/test_audit_regressions.py`, trừ khi có ghi tên tệp khác.

---

## 5. Hạn chế còn lại và quyết định giữ nguyên

- **D4 (giữ nguyên):** quota bị trừ khi provider lỗi, để chống lạm dụng.
- **Rò rỉ thời gian còn sót lại ở mức mili-giây:** với email có thật, server vẫn tạo token trong DB trước khi trả lời. Phần lớn độ trễ (SMTP) đã được loại bỏ.
- **Embed và client không phải trình duyệt:** `Origin` và `X-Embed-Parent-Origin` đều giả được nếu gọi API bằng script. Embed token vốn là công khai, nên `allowed_origins` chỉ ngăn **website khác nhúng widget**, không phải lớp xác thực.
- **Email trùng hoa/thường có sẵn từ trước:** nếu DB đã có hai tài khoản như vậy, đăng nhập không phân biệt hoa/thường sẽ từ chối thay vì chọn bừa. Cần gộp tay hai tài khoản đó.

---

## 6. Vùng đã xác minh là đúng

- `purpose` của JWT chặn việc dùng token reset hay verify làm access token.
- Token xác minh email gắn hash của email, nên đổi email thì link cũ mất hiệu lực.
- Webhook Stripe dùng `billing_sync_version` đơn điệu.
- `require_admin` đọc role từ DB ở mỗi request.
- `/metrics` đóng khi ở production mà thiếu token; token được so sánh theo thời gian hằng.
- CORS ở production yêu cầu khai báo origin tường minh.

---

## 7. Kiểm thử

- **Backend:** xem số liệu tổng ở `../01_Technical_Code_Audit_Report.md` §7. Mọi test trong bảng §4 đều **FAIL trên `git archive HEAD`** và PASS sau khi sửa. Riêng `test_auth_providers.py` được sửa có chủ đích theo quyết định D1: tài khoản chưa xác minh mất mật khẩu sau khi liên kết Google; tài khoản đã xác minh giữ nguyên.
- **Trình duyệt:** chạy `next start` từ bản build production có `NEXT_PUBLIC_API_URL`, mở `/embed?token=x&api=https://evil.example` và gửi một câu hỏi. Request đi tới API đã cấu hình, không tới `evil.example`. Ở bản build không có biến này, CSP chặn request tới `evil.example` (console báo vi phạm `connect-src`).
- **Chưa kiểm:** Stripe thật (chỉ dùng fake SDK) và Google thật (dùng chữ ký giả lập trong test).
