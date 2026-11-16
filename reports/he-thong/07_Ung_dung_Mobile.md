# Hệ thống 7: Ứng dụng mobile (Expo / React Native) cho kỹ thuật viên

**Phạm vi:**

- `mobile/lib/api.ts`
- `mobile/lib/offlineOrigin.ts`
- `mobile/lib/fieldOffline.ts`
- `mobile/screens/`, `mobile/components/`
- `tests/client_state.test.cjs` (phần mobile)

---

## 1. Vai trò

Kỹ thuật viên làm việc ngoài hiện trường, thường mất sóng. Ứng dụng làm ba việc:

1. Cache tổng quan đội và chi tiết ca vào **SecureStore**, đã mã hoá theo thiết bị.
2. Xếp các thao tác (đổi trạng thái, cập nhật checklist, ghi chú) vào outbox, rồi đồng bộ khi có mạng.
3. Gắn dữ liệu offline với **đúng máy chủ** đã tạo ra nó. Hai máy chủ có thể trùng ID người dùng, nên dữ liệu của máy chủ này không được đọc hay gửi lên máy chủ khác.

---

## 2. Luồng và cơ chế

### 2.1 Lưu trữ an toàn (`fieldOffline.ts`)

- **Chia chunk:** SecureStore giới hạn kích thước mỗi khoá, nên mỗi bản ghi được chia thành các chunk ≤ 1200 byte UTF-8.
- **Ghi:** `writeRecord` ghi một *generation* mới, đổi `manifest`, rồi mới xoá generation cũ.
- **Khoá:** mọi thao tác đọc-sửa-ghi chạy trong `withStorageLock`, một hàng đợi promise.
- **Đọc (U18, đã sửa):** các hàm đọc công khai (`getCachedActiveFieldOverview`, `getCachedFieldCase`, `getPendingFieldActionCount`, `getRejectedFieldActions`) và snapshot lúc đồng bộ giờ cũng đi qua khoá. Trước đây, một lượt đọc chen vào giữa việc đổi manifest và xoá generation cũ sẽ gặp lỗi tạm "Bản lưu offline chưa đầy đủ".

### 2.2 Gắn dữ liệu offline với máy chủ (`offlineOrigin.ts`, U1, đã sửa)

| Thao tác | Trước khi sửa | Sau khi sửa |
|---|---|---|
| Đọc (`assertOfflineApiOrigin`) | Tự gắn origin hiện tại nếu chưa gắn | Chỉ **kiểm tra**: từ chối khi đã gắn với máy chủ khác; chưa gắn thì cho đọc (chưa có gì để lộ) |
| Ghi (`bindOfflineApiOriginForWrite`) | Như đọc | Gắn origin hiện tại ở **lần ghi đầu tiên** và đặt marker `has_records`; từ chối nếu đã gắn với máy chủ khác |
| Đổi máy chủ (`setApiUrl` → `rememberOfflineApiOrigin`) | Luôn gắn origin **cũ** | Chỉ gắn khi đã có dữ liệu offline (có marker) mà chưa gắn |

**Kịch bản lỗi cũ:**

1. Bản cài mới có địa chỉ mặc định `http://100.121.99.57:8001`, là một IP dev.
2. Người dùng đổi sang máy chủ thật trước khi có thao tác offline nào.
3. Ứng dụng đã khoá vùng offline vào IP dev, nên từ đó mọi chức năng offline trên máy chủ thật đều báo "máy chủ khác".

**Thêm:** `DEFAULT_API_URL` giờ lấy từ `EXPO_PUBLIC_API_URL` khi có, để bản phát hành không mang địa chỉ dev.

**Bảo toàn hành vi đã có:** dữ liệu tạo trên máy chủ A vẫn không đọc hay gửi được khi chọn máy chủ B, và quay lại A thì khôi phục được (test `changing backend cannot read or replay…`).

### 2.3 Đồng bộ (`runFieldSync`)

1. Chụp snapshot hàng đợi.
2. Xử lý theo từng ca, giữ đúng thứ tự trong mỗi ca.
3. Trước mỗi request, kiểm tra phiên vẫn là của đúng người dùng (`isSessionCurrent`).
4. Xử lý lỗi:

   | Lỗi | Xử lý |
   |---|---|
   | Lỗi mạng | Dừng; giữ lại toàn bộ |
   | 4xx vĩnh viễn (trừ 401, 408, 429) | Chuyển sang `rejected_actions` để người dùng xem lại |
   | **5xx (D6, đã sửa)** | Tăng `server_failures` của thao tác. Đến lần thứ 8 thì chuyển sang `rejected` và gửi tiếp các thao tác sau của ca. Dưới 8 lần thì dừng ở ca đó như trước |

   "Thử lại" thủ công xoá bộ đếm.
5. Server bảo đảm idempotency theo `client_action_id` (xem Hệ thống 2).

### 2.4 Giao diện chat

Ô nhập câu hỏi giới hạn 4000 ký tự (`maxLength`), khớp với backend.

---

## 3. Lỗi đã sửa

| ID | Mức | Vị trí | Kịch bản lỗi trước khi sửa | Test |
|---|---|---|---|---|
| U1 | **Cao** | `lib/offlineOrigin.ts`, `lib/fieldOffline.ts`, `lib/api.ts` | Bản cài mới đổi sang máy chủ thật thì mất vĩnh viễn chế độ offline. | `client_state: fresh install that switches to the real server keeps offline mode…` |
| D6 | TB | `lib/fieldOffline.ts:runFieldSync` | Một thao tác luôn lỗi 500 chặn mọi chỉnh sửa sau của cùng ca. | `client_state: mobile sync moves an action that keeps failing with 5xx…` |
| U18 | Thấp | `lib/fieldOffline.ts` | Lỗi tạm "Bản lưu offline chưa đầy đủ" khi đọc chen giữa lúc ghi. | Kiểm bằng đọc code. Toàn bộ test mobile hiện có vẫn PASS |
| F6 (UI) | Thấp | `screens/ChatScreen.tsx` | Ô nhập không giới hạn độ dài. | `tsc` |

---

### 2.5 Khôi phục máy bị bản cũ khoá nhầm (vòng 3, `releaseUnusedLegacyBinding`)

Khi `isSecureOfflineStorageAvailable()` thấy vùng offline đang gắn với máy chủ khác, nó gỡ ràng buộc chỉ khi **cả ba** điều kiện sau đúng:

1. Ràng buộc trỏ tới máy chủ khác máy chủ hiện tại.
2. **Không có marker `has_records`.** Bản mới ghi marker kèm mọi bản ghi offline, nên thiếu marker nghĩa là ràng buộc do bản cũ tạo ra, thường chỉ vì một lần đọc.
3. Người dùng đang đăng nhập **không có bản ghi gốc nào**. Code kiểm manifest của `actions`, `rejected_actions`, `case_index` và `active_workspace` trực tiếp trong SecureStore, không đi qua khoá ràng buộc nên không bị deadlock.

Thoả cả ba thì ràng buộc bị xoá, và lần ghi tiếp theo gắn vào máy chủ hiện tại. Nếu người dùng có dữ liệu thật trên máy chủ cũ, dữ liệu đó **vẫn được cô lập** như trước.

## 4. Hạn chế còn lại

- **Trường hợp biên khi gỡ ràng buộc:** SecureStore không liệt kê được khoá, nên code chỉ kiểm được dữ liệu của người dùng **đang đăng nhập**. Nếu cùng máy còn dữ liệu của *người khác* từ máy chủ cũ, và sau này một tài khoản trùng ID trên máy chủ mới đăng nhập, dữ liệu đó có thể đọc được. Trường hợp này rất hiếm vì mỗi máy thường thuộc một kỹ thuật viên.
- **Chưa chạy trên thiết bị thật.** Mới chạy `tsc` và các test Node mô phỏng SecureStore và `fetch`.

---

## 5. Kiểm thử

| Kiểm tra | Kết quả |
|---|---|
| `npm ci` → `tsc --noEmit` | PASS |
| `node --test tests/client_state.test.cjs` | 19/19 |
| Test mobile mới (2 test ở vòng 2, 1 test ở vòng 3) | FAIL trên mã trước vòng tương ứng, PASS sau khi sửa |

Test vòng 3 là `device locked to the dev server by an old version recovers offline mode unless it holds that user's data`. Nó kiểm cả hai nhánh: máy không có dữ liệu thì được khôi phục; máy có dữ liệu của người dùng đó thì vẫn bị cô lập.
