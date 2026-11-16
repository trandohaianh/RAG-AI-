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

### 2.1 Phiên đăng nhập

- **Lưu trữ:** JWT nằm trong `localStorage`; axios gắn header `Authorization`.
- **Xử lý 401:** một phản hồi 401 chỉ xoá đúng token đã gửi request đó, không xoá phiên mới hơn (test `web Axios and streaming 401s…`).

### 2.2 Content-Security-Policy (U16, đã sửa, `next.config.js`)

CSP chỉ bật ở bản build production; chế độ dev cần eval và websocket cho hot reload.

```
default-src 'self'
script-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/client
style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style
img-src 'self' data: blob: https:
font-src 'self' data:
connect-src 'self' <origin của NEXT_PUBLIC_API_URL> https://accounts.google.com/gsi/
frame-src https://accounts.google.com/gsi/
object-src 'none'; base-uri 'self'; form-action 'self'
frame-ancestors 'self'      (trang /embed: frame-ancestors *)
```

**Phần CSP chặn được:**

- script từ domain lạ;
- gửi dữ liệu (kể cả token) ra domain lạ;
- `<object>` và `<base>`;
- nhúng console vào site khác.

**Giới hạn:** App Router chèn script inline khi hydrate, nên `script-src` vẫn cần `'unsafe-inline'` vì chưa dùng nonce. Một XSS chèn được script inline vẫn chạy được, nhưng không gửi được token ra ngoài qua `fetch` hay XHR tới domain lạ.

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

## 4. Kiểm thử

| Kiểm tra | Kết quả |
|---|---|
| `npm ci` → `npm run typecheck` | PASS |
| `npm run build` | PASS, 19 route |
| `node --test tests/client_state.test.cjs` | **17/17** (14 cũ + 3 mới). 3 test mới FAIL trên mã gốc |
| `next start` (build có `NEXT_PUBLIC_API_URL`) và built-in browser | Xem dưới |

Chi tiết kiểm tra trên trình duyệt:

- `/login`, `/` và `/embed` render đầy đủ; console không có vi phạm CSP. Lỗi duy nhất là `ERR_CONNECTION_REFUSED` vì API cục bộ không chạy.
- Header CSP đúng theo từng path.
- `/embed?api=https://evil.example`: câu hỏi đi tới API đã cấu hình.
- Ở bản build không có biến môi trường, CSP chặn kết nối tới `evil.example`.

**Chưa kiểm:** đăng nhập Google thật dưới CSP (cần client ID thật), và trang `/field/schedule` với tile OpenStreetMap (cần đăng nhập và API).

---

## 5. Lưu ý môi trường

`frontend/node_modules` trong repo cũ hơn `package.json` (thiếu `leaflet`), nên `npm run typecheck` báo TS2307. Đây là lỗi môi trường. Chạy `npm ci` trước khi kiểm tra.
