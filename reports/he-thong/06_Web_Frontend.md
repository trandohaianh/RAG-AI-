# Hệ thống 6: Web frontend (Next.js) — console, widget nhúng, outbox offline

**Phạm vi:** `frontend/` (App Router, `lib/api.ts`, `lib/auth.tsx`, `lib/offlineQueue.ts`, `lib/caseCache.ts`, `components/`, `next.config.js`) và `tests/client_state.test.cjs`.

---

## 1. Vai trò

| Thành phần | Nội dung |
|---|---|
| Console | Chat RAG, tài liệu, ca hiện trường, lịch tuyến (Leaflet và OpenStreetMap), tích hợp CRM/NMS, analytics, chi phí, quản trị |
| Trang `/embed` | Widget chat được iframe trên website khách |
| Outbox offline | Lưu mọi thao tác ca vào IndexedDB trước khi gửi |

---

## 2. Luồng và cơ chế

### 2.1 Phiên đăng nhập: cookie httpOnly (vòng 3, `lib/api.ts`, `lib/auth.tsx`)

**Đăng nhập**

- Request đăng nhập (mật khẩu, đăng ký, Google) gửi kèm `X-Auth-Mode: cookie`.
- API đặt cookie `fieldops_session` với httpOnly, SameSite=Lax, và Secure ngoài môi trường dev. Body trả về `access_token: ""`.
- **JavaScript không bao giờ thấy token.**

**Mọi request sau đó**

- axios dùng `withCredentials: true`; fetch SSE dùng `credentials: "include"`; upload qua XHR dùng `withCredentials`.
- Header `X-Requested-With: fieldops` luôn được gửi. Backend bắt buộc header này cho phương thức ghi khi xác thực bằng cookie (chống CSRF).

**`localStorage` chỉ còn hai khoá**

- `rag_user`: hồ sơ hiển thị, không phải bí mật.
- `rag_session`: mã phiên ngẫu nhiên đặt lúc đăng nhập. Mỗi request ghi lại mã phiên tại thời điểm gửi; một phản hồi 401 chỉ đăng xuất **đúng phiên đó**, nên phản hồi trễ của phiên cũ không đăng xuất phiên mới (test `web Axios and streaming 401s…`).

**Đăng xuất:** gọi `POST /auth/logout` để server xoá cookie (JS không tự xoá được cookie httpOnly).

**Chuyển phiên cũ:** nếu còn token cũ `rag_token` trong `localStorage`, app gọi `POST /auth/session` với Bearer để đổi sang cookie rồi xoá token. Lần chuyển dùng **một promise chung**: `Providers` mount `AuthProvider` hai lần, và lần chạy thứ hai từng kết luận nhầm "chưa đăng nhập". Lỗi này phát hiện được khi chạy end-to-end và đã sửa.

**Triển khai khác site:** nếu API và web khác tên miền gốc, đặt `AUTH_COOKIE_SAMESITE=none` (tự bật Secure) và `CORS_ORIGINS`.

### 2.2 Content-Security-Policy có nonce (vòng 3, `proxy.ts`)

Mỗi request trang sinh một nonce 128 bit (Next 16 "proxy"). Toàn bộ trang render theo từng request (`await connection()` trong `app/layout.tsx`) để Next gắn nonce vào script của nó.

```
script-src 'self' 'nonce-<ngẫu nhiên>' 'strict-dynamic' https://accounts.google.com/gsi/client
style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style
img-src 'self' data: blob: https:;  font-src 'self' data:
connect-src 'self' <origin của NEXT_PUBLIC_API_URL> https://accounts.google.com/gsi/
frame-src https://accounts.google.com/gsi/;  object-src 'none';  base-uri 'self';  form-action 'self'
frame-ancestors 'self'      (trang /embed: frame-ancestors *)
```

- **Không còn `'unsafe-inline'` cho script.** Script inline, event handler như `onerror=` và `<iframe srcdoc><script>` do XSS chèn vào đều bị chặn. Đã kiểm trên trình duyệt (§4).
- `'strict-dynamic'` cho phép các chunk do script hợp lệ nạp, kể cả script Google Sign-In.
- `style-src` vẫn cần `'unsafe-inline'` vì thuộc tính `style` của React và Leaflet không mang nonce được; CSS không thực thi mã.
- Chế độ dev thêm `'unsafe-eval'` và `ws:` cho hot reload.

### 2.3 Widget nhúng (`app/embed/page.tsx`, đã sửa)

- **Địa chỉ API:** dùng `NEXT_PUBLIC_API_URL`; tham số `?api=` chỉ được đọc khi biến này không có (bản dev).
- **Header `X-Embed-Parent-Origin`:** lấy từ `location.ancestorOrigins[0]`, hoặc từ origin của `document.referrer`. Backend so với `allowed_origins` của token (xem Hệ thống 1).
- **Độ dài câu hỏi:** tối đa 4000 ký tự, khớp với backend.

### 2.4 Outbox offline (`lib/offlineQueue.ts`)

**Quy trình gửi**

1. Lưu thao tác vào IndexedDB **trước** khi gửi, gắn `client_action_id` (UUID).
2. Phạm vi theo bộ ba (API origin, user, workspace).
3. Web Locks tuần tự hoá việc gửi giữa các tab.

**Xử lý lỗi**

| Phản hồi | Xử lý |
|---|---|
| Lỗi mạng, 401, 408, 429 | Giữ nguyên, thử lại sau, không đếm |
| **5xx (D6, đã sửa)** | Tăng `serverFailures`. Đến lần thứ 8 (`MAX_SERVER_FAILURES`), thao tác chuyển sang `rejected` với thông báo rõ ràng, và các thao tác phía sau được gửi tiếp |
| 4xx nghiệp vụ | Chuyển sang `rejected` ngay |

Trước bản sửa, một payload mà máy chủ luôn trả 500 chặn mọi thao tác phía sau vĩnh viễn. "Thử lại" thủ công đặt bộ đếm về 0.

### 2.5 Phân quyền trên giao diện (D2)

Khối "Điều phối" (toạ độ, khung giờ, ưu tiên) của một ca chỉ hiển thị với trưởng đội, khớp với kiểm tra quyền ở backend.

---

## 3. Lỗi đã sửa

| ID | Mức | Vị trí | Kịch bản lỗi trước khi sửa | Test |
|---|---|---|---|---|
| D6 | TB | `lib/offlineQueue.ts` | Một thao tác lỗi 500 vĩnh viễn chặn mọi chỉnh sửa offline phía sau. | `client_state: web outbox moves an action that keeps failing with 5xx…` |
| U16 | TB | `next.config.js` | Không có CSP: XSS bất kỳ đọc được token và gửi đi bất cứ đâu. | `curl -I` kiểm tra header; trình duyệt: các trang render bình thường, không có vi phạm CSP, và request tới domain lạ bị chặn (§4) |
| U16b | Thấp | `app/embed/page.tsx` | `?api=` gửi câu hỏi của khách tới máy chủ tuỳ ý. | Trình duyệt (§4) |
| F6 (UI) | Thấp | `ChatWindow.tsx`, `embed/page.tsx` | Ô nhập không giới hạn độ dài trong khi backend giới hạn 4000. | Typecheck và build |
| D2 (UI) | Thấp | `app/(app)/field/page.tsx` | Kỹ thuật viên thấy form điều phối nhưng luôn nhận 403. | Typecheck và build |
| F20 | Thấp (test) | `tests/client_state.test.cjs` | Mock thiếu `CasePhotosSection`, nên một test luôn fail. | 13/14 → 14/14 |

---

### Vòng 3

| ID | Mức | Vị trí | Trước | Test |
|---|---|---|---|---|
| V2a | TB | `lib/api.ts`, `lib/auth.tsx`, trang đăng nhập/đăng ký, Google, `UploadZone`, `field/page.tsx` | Token trong `localStorage`: XSS bất kỳ đọc được phiên | `client_state: web session lives in an httpOnly cookie…`, test 401 viết lại theo mã phiên; end-to-end trên trình duyệt |
| V2b | TB | `proxy.ts`, `app/layout.tsx`, `next.config.js` | CSP phải mở `'unsafe-inline'` cho script | Trình duyệt: nonce có trên 17/21 script; HTML chèn vào bị chặn |

## 4. Kiểm thử

| Kiểm tra | Kết quả |
|---|---|
| `npm ci` → `npm run typecheck` | PASS |
| `npm run build` | PASS, 19 route |
| `node --test tests/client_state.test.cjs` | **19/19**. Các test mới của từng vòng đều FAIL trên mã trước vòng đó |
| `next start` (build có `NEXT_PUBLIC_API_URL`) và built-in browser | Xem dưới |

Chi tiết kiểm tra trên trình duyệt:

- `/login`, `/` và `/embed` render đầy đủ; console không có vi phạm CSP. Lỗi duy nhất là `ERR_CONNECTION_REFUSED` vì API cục bộ không chạy.
- Header CSP đúng theo từng path.
- `/embed?api=https://evil.example`: câu hỏi đi tới API đã cấu hình.
- Ở bản build không có biến môi trường, CSP chặn kết nối tới `evil.example`.

**End-to-end vòng 3** (API thật trên WSL với DB tạm, `next start` bản production, built-in browser):

1. Đăng ký tài khoản test rồi vào `/chat`. `localStorage` chỉ còn `rag_user` và `rag_session`; `document.cookie` rỗng.
2. Reload vẫn giữ phiên. POST tạo hội thoại qua cookie thành công.
3. Đăng xuất làm `/auth/me` trả 401.
4. Đặt token cũ vào `localStorage` rồi mở `/chat`: app tự đổi sang cookie, người dùng ở lại `/chat/1`, token cũ bị xoá.
5. Chèn `<img onerror>` và `<iframe srcdoc><script>`: **bị CSP chặn**. Console không có vi phạm CSP nào khác.

**Chưa kiểm:** đăng nhập Google thật dưới CSP (cần client ID thật), và trang `/field/schedule` với tile OpenStreetMap.

---

## 5. Lưu ý môi trường

`frontend/node_modules` trong repo cũ hơn `package.json` (thiếu `leaflet`), nên `npm run typecheck` báo TS2307. Đây là lỗi môi trường. Chạy `npm ci` trước khi kiểm tra.
