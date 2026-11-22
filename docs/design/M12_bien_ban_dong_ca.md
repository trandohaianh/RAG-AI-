# M12 — Biên bản đóng ca: bản thiết kế

- **Trạng thái:** **ĐÃ DUYỆT (2026-11-22).** Chủ dự án chọn phương án khuyến nghị ở cả 4 câu (mục 7). Chưa viết code.
- **PRD:** `docs/PRD.md` §5.1 M12.
- **Ước lượng:** khoảng 3 ngày, gồm kiểm thử. Đây là tính năng lớn nhất của giai đoạn 1.

## 1. Mục tiêu (giải thích dễ hiểu)

Hiện nay kỹ thuật viên bấm **"Hoàn tất"** là ca đóng ngay, không ghi lại kết quả gì. Khi nghiệm thu với nhà mạng hoặc khi khách khiếu nại thì **không có bằng chứng** ca đã được xử lý ra sao và khách có đồng ý hay không.

M12 thay nút đó bằng **biên bản đóng ca**. Muốn đóng ca phải điền **mã kết quả**, **ghi chú**, và lấy **chữ ký của khách** (khách ký bằng ngón tay trên màn hình). Biên bản được lưu lại, xem lại được trong ca, và báo về CRM nếu ca đến từ CRM. Việc này làm được cả khi mất sóng.

Tiêu chí nghiệm thu (PRD):
1. Không đóng được ca nếu thiếu mã kết quả, ghi chú hoặc chữ ký khách (trừ trường hợp ở Q2).
2. Biên bản xem lại được trong ca: mã, ghi chú, người ký, chữ ký, ai đóng, lúc nào.
3. Ca từ CRM: webhook báo ngược kèm mã kết quả.
4. Làm được khi offline và đồng bộ sau.

## 2. Hiện trạng (đã đọc code)

- **Đóng ca hôm nay:** `PATCH /field/cases/{id}` với `status=resolved`. Trên mobile, nút "Hoàn tất" chạy qua hàng đợi offline (`kind: "status"`). Trên web, nút trạng thái trên trang ca.
- **CRM** có thể tự đóng ca: sự kiện `ticket.closed` và `alarm.cleared` trong `backend/field/integrations.py`.
- **Ảnh hiện trường** (`backend/field/photos.py`):
  - ảnh được mã hoá lại và bỏ GPS;
  - lưu ngoài thư mục web, chỉ xem qua link có hạn 15 phút;
  - có trong bản sao lưu và chịu chính sách tự xoá sau N ngày.
  - Chữ ký sẽ **đi chung cơ chế này**.
- **Mobile chưa có thư viện vẽ:** không có `react-native-svg` hay WebView, nên cần thêm một thư viện (xem 3.3).
- `resolved_at` (giờ đóng ca) đã có sẵn và tự cập nhật khi trạng thái đổi.

## 3. Thiết kế

### 3.1 Dữ liệu — bảng mới `field_case_closures` (migration 8)

| Cột | Nghĩa |
|---|---|
| `id`, `case_id` | khoá; một ca có thể có **nhiều** biên bản nếu bị mở lại rồi đóng lại; bản mới nhất là bản hiệu lực |
| `outcome_code` | mã kết quả (Q1) |
| `note` | ghi chú (bắt buộc, 3–2000 ký tự) |
| `customer_name` | tên người ký (tuỳ chọn) |
| `signature_photo_id` | ảnh chữ ký, lưu như một ảnh hiện trường có nhãn "Chữ ký khách hàng"; trống nếu Q2 cho phép không ký |
| `closed_by`, `closed_at` | ai đóng, lúc nào |
| `client_action_id` | chống gửi trùng khi đồng bộ offline |

Dữ liệu cá nhân: chữ ký và tên người ký là **dữ liệu cá nhân của khách**. Chúng chịu **cùng chính sách tự xoá** với ảnh hiện trường (`FIELD_PHOTO_RETENTION_DAYS`, mặc định 365 ngày sau khi đóng ca) và nằm trong bản sao lưu.

### 3.2 API (backend)

**`POST /field/cases/{id}/close`**, gửi JSON:
- `outcome_code`, `note`, `customer_name`;
- `signature`: các nét ký dưới dạng danh sách điểm. Gọn, nên gửi được qua hàng đợi offline. **Máy chủ tự vẽ thành ảnh PNG**, nên không phụ thuộc thiết bị;
- `client_action_id`.

Xử lý:
1. Quyền theo Q3 (403 nếu không đủ quyền; người ngoài đội 404).
2. Kiểm tra theo Q2 (422, kèm thông báo rõ thiếu gì). Ca đã đóng → 409.
3. **Trong cùng một giao dịch:**
   - vẽ và lưu ảnh chữ ký;
   - ghi biên bản;
   - chuyển ca sang `resolved`;
   - ghi nhật ký: "Đóng ca — Kết quả: Đã sửa xong — có chữ ký khách (Nguyễn Văn A)";
   - nếu ca từ CRM: đưa webhook `case.status_changed` kèm phần `closure` vào hàng đợi gửi (Q4).
4. Gửi lại cùng `client_action_id` → trả kết quả cũ, không tạo biên bản thứ hai.

**`GET /field/cases/{id}`** trả thêm `closure` (biên bản hiệu lực) gồm link ảnh chữ ký có hạn.

**Đường đóng ca cũ:** `PATCH status=resolved` từ giao diện bị từ chối (409 "Đóng ca bằng biên bản") theo Q3. **CRM đóng từ xa** (`ticket.closed`, `alarm.cleared`) **không cần biên bản**, vì đó là hệ thống ngoài quyết định; nhật ký ghi rõ "đóng từ CRM".

**Mở lại ca:** giữ nguyên biên bản cũ làm lịch sử. Đóng lại thì cần biên bản mới.

### 3.3 Mobile

- **Thêm thư viện `react-native-svg`** (thư viện vẽ chuẩn được Expo hỗ trợ, miễn phí) để vẽ nét ký mượt trong lúc khách ký.
- Màn mới **"Biên bản đóng ca"**, mở thay cho nút "Hoàn tất":
  1. chọn mã kết quả;
  2. ghi chú;
  3. tên người ký;
  4. khung ký, có nút "Ký lại";
  5. nút **"Đóng ca"**.
- **Offline:** biên bản (cả nét ký) được xếp vào **hàng đợi an toàn đã có**, dưới dạng loại thao tác mới `close`, rồi tự gửi khi có mạng. Ca hiện "Đã đóng — chờ đồng bộ".
- Trong chi tiết ca: hiện **biên bản** gồm mã, ghi chú, người ký, ảnh chữ ký, người đóng và thời gian.

### 3.4 Web

Hộp thoại "Đóng ca" có các trường như trên và một khung ký (dùng `<canvas>` của trình duyệt, không cần thư viện). Hộp thoại này dùng được khi trưởng đội hoặc thợ đóng ca trên máy tính hay máy tính bảng. Phần hiển thị biên bản trong ca cũng giống mobile.

## 4. File sẽ tạo / sửa

| File | Việc |
|---|---|
| `backend/field/models.py`, `backend/schema_migrations.py` | bảng `field_case_closures` + migration 8 |
| `backend/field/closures.py` (mới) | API đóng ca, vẽ chữ ký PNG (Pillow, đã có sẵn) |
| `backend/field/schemas.py`, `backend/field/router.py` | `closure` trong ca; chặn `PATCH resolved` (Q3) |
| `backend/field/integrations.py` | phần `closure` trong webhook (Q4) |
| `backend/field/photos.py` | tạo ảnh chữ ký dùng chung đường lưu |
| `mobile/package.json` | thêm `react-native-svg` |
| `mobile/screens/CloseCaseScreen.tsx` (mới), `mobile/components/SignaturePad.tsx` (mới) | màn biên bản, khung ký |
| `mobile/lib/fieldOffline.ts`, `mobile/lib/api.ts` | thao tác offline `close` |
| `mobile/screens/FieldCaseDetailScreen.tsx` | nút "Đóng ca" → màn biên bản; hiện biên bản |
| `frontend/components/field/CloseCaseDialog.tsx` (mới), `frontend/components/field/SignatureCanvas.tsx` (mới) | web |
| `frontend/app/(app)/field/page.tsx` | gắn hộp thoại (vài dòng) |
| `tests/test_field_case_closure.py` (mới), `tests/client_state.test.cjs` | test |

## 5. Kiểm thử sẽ viết

- Đóng ca đủ thông tin → ca `resolved`; có đúng 1 biên bản, 1 ảnh chữ ký PNG xem được qua link có hạn, và 1 dòng nhật ký.
- Thiếu mã, ghi chú hoặc chữ ký → 422, ca vẫn mở. Trường hợp không cần chữ ký theo Q2.
- Quyền theo Q3: thợ không được giao → 403; người ngoài đội → 404.
- `PATCH status=resolved` từ giao diện → 409 (Q3). CRM `ticket.closed` vẫn đóng được.
- Gửi lại offline cùng `client_action_id` → chỉ 1 biên bản.
- Mở lại rồi đóng lại → 2 biên bản; bản mới nhất là bản hiệu lực.
- Ca từ CRM → webhook có `closure` đúng Q4; ca nhập tay không gửi webhook.
- Chữ ký: nét quá nhiều hoặc quá dài → 422; chữ ký trống (không có nét nào) → bị coi là chưa ký.
- Migration 8 trên CSDL cũ và CSDL mới; toàn bộ test cũ vẫn đạt.
- Client test: hàng đợi offline với thao tác `close`.

**Giới hạn kiểm chứng:**
- Không có điện thoại thật. Màn ký trên mobile sẽ được thử trên bản web của Expo (vẽ bằng chuột).
- Cảm giác ký bằng ngón tay trên điện thoại cần bạn thử lại.
- Chế độ offline trên điện thoại chỉ được kiểm bằng test tự động.

## 6. Ảnh hưởng tới phần đang chạy

- **Thay đổi hành vi:** nút "Hoàn tất" cũ không còn đóng ca thẳng (Q3).
- **Thêm thư viện** `react-native-svg` vào app mobile: lần phát hành app sau cần build lại, không cập nhật qua OTA được nếu bản cài cũ chưa có thư viện này.
- **CSDL:** thêm bảng mới (migration 8), không sửa bảng cũ.
- M14 đã chặn sửa thông tin ca khi ca đã đóng, nên biên bản luôn khớp với thông tin lúc đóng.

## 7. Các câu hỏi cần chủ dự án chọn

| | Câu hỏi | **Đã chọn (2026-11-22)** |
|---|---|---|
| Q1 | Danh mục mã kết quả | **6 mã cố định:** Đã sửa xong (`fixed`) · Thay thiết bị (`replaced`) · Không tái hiện lỗi (`not_reproduced`) · Chuyển cấp kỹ thuật (`escalated`) · Khách vắng nhà (`customer_absent`) · Khách từ chối sửa (`customer_refused`) |
| Q2 | Lúc nào bắt buộc chữ ký? | **Bắt buộc, trừ** "Khách vắng nhà" và "Khách từ chối sửa". Hai mã này thay chữ ký bằng **ghi chú + ít nhất 1 ảnh hiện trường** |
| Q3 | Ai được đóng ca, nút "Hoàn tất" cũ thì sao? | **Luôn qua biên bản:** kỹ thuật viên được giao và trưởng đội; `PATCH status=resolved` từ giao diện → 409; chỉ CRM được đóng từ xa |
| Q4 | Gửi gì về CRM? | **Kết quả, không gửi ảnh ký:** mã kết quả, ghi chú, `signed: true/false`, tên người ký |
