# M14 — Sửa thông tin ca: bản thiết kế

- **Trạng thái:** **ĐÃ DUYỆT (2026-11-22).** Chủ dự án chọn phương án khuyến nghị ở cả 4 câu (mục 7). Chưa viết code.
- **PRD:** `docs/PRD.md` §5.1 M14.
- **Ước lượng công sức:** khoảng 1 ngày làm việc, gồm cả kiểm thử.

## 1. Mục tiêu (giải thích dễ hiểu)

Hiện nay, sau khi tạo ca, trưởng đội **không sửa được** tiêu đề, triệu chứng, địa chỉ hay model thiết bị. Gõ nhầm thì phải tạo ca mới hoặc ghi chú bù.

M14 cho **trưởng đội** sửa các thông tin này. Mỗi lần sửa được **ghi nhật ký**: ai sửa, sửa trường nào, giá trị cũ → mới. Kỹ thuật viên chỉ xem, không sửa.

Tiêu chí nghiệm thu, lấy từ PRD:
1. Trưởng đội sửa được tiêu đề, triệu chứng (kèm thông tin bàn giao), tên khách, địa chỉ, model thiết bị, số serial.
2. Kỹ thuật viên **không** sửa được các trường này (máy chủ trả 403).
3. Mỗi lần sửa có một dòng nhật ký: ai sửa, trường nào, cũ → mới.
4. Ca đến từ CRM: lần CRM cập nhật sau vẫn áp dụng theo quy tắc hiện có.

## 2. Hiện trạng (đã đọc code)

- `PATCH /field/cases/{id}` (`backend/field/router.py`, hàm `update_case`) chỉ nhận `status` và `assigned_to`. Quyền: trưởng đội sửa mọi thứ; kỹ thuật viên chỉ nhận ca và đổi trạng thái ca của mình.
- Toạ độ, ưu tiên và khung giờ được sửa qua `PATCH /field/cases/{id}/dispatch` (chỉ trưởng đội). M14 **không** đụng vào phần này.
- CRM sửa ca qua `_apply_fields` (`backend/field/integrations.py`), ghi sự kiện `external`.
- Nhật ký ca: bảng `field_service_case_events`; hàm `_add_event` đã có sẵn.
- Chống gửi trùng (offline): cơ chế `client_action_id` (`_claim_client_action`) đã có sẵn.
- Trên web, trang `frontend/app/(app)/field/page.tsx` đã dài 1067 dòng. Form sửa sẽ nằm trong **component mới**, không nhồi thêm vào trang này (quy ước `docs/CONVENTIONS.md` §3).

## 3. Thiết kế

### 3.1 API (backend)

**Endpoint mới:** `PATCH /field/cases/{case_id}/details`. Tách riêng khỏi `PATCH /field/cases/{id}`, vì quy tắc quyền của hai loại thay đổi khác nhau. Gộp vào thì dễ làm hỏng phần đang chạy tốt.

Body (mọi trường đều tuỳ chọn; chỉ trường được gửi mới bị đổi):

| Trường | Kiểu và giới hạn (giống lúc tạo ca) |
|---|---|
| `title` | 3–255 ký tự |
| `symptom` | 3–5000 ký tự |
| `customer_label` | ≤ 160, cho phép xoá (null) |
| `address` | ≤ 500, cho phép xoá |
| `device_model` | ≤ 160, cho phép xoá |
| `serial_number` | ≤ 160, cho phép xoá |
| `expected_updated_at` | thời điểm sửa cuối mà màn hình đang thấy (xem quyết định Q3) |
| `client_action_id` | chống gửi trùng, như các thao tác khác |

Xử lý:
1. Kiểm tra người gọi là thành viên đội; nếu không phải thì 404, giống các API ca khác.
2. Chỉ trưởng đội; nếu không thì 403 "Chỉ trưởng đội được sửa thông tin ca".
3. Kiểm tra trạng thái ca (Q4) và xung đột (Q3).
4. So từng trường. Không có gì đổi thì trả về ca và **không** ghi nhật ký.
5. Ghi **một** sự kiện `details_updated`, ví dụ: `Sửa thông tin: Địa chỉ: "12 Lê Lợi" → "21 Lê Lợi"; Model: "HG8145V5" → "HG8245H"`. Mỗi giá trị trong nhật ký cắt ở 120 ký tự; triệu chứng dài chỉ ghi "đã sửa (N ký tự)".
6. Nếu địa chỉ đổi thì xử lý toạ độ theo Q2.
7. Webhook báo CRM theo Q1.

**Schema mới:** `CaseDetailsUpdate`, đặt trong `backend/field/schemas.py`.

### 3.2 Giao diện web

- Component mới `frontend/components/field/CaseDetailsEditor.tsx`, cùng kiểu với `CaseDispatchEditor.tsx` đã có.
- Chỉ hiện nút **"Sửa thông tin"** khi người xem là trưởng đội. Bấm vào thì hiện form với 6 trường, giá trị hiện tại điền sẵn.
- Bấm **Lưu**:
  - thành công → đóng form và hiện thông tin mới; nhật ký có dòng mới;
  - 409 (người khác vừa sửa) → báo "Ca vừa được cập nhật ở nơi khác. Tải lại để xem bản mới rồi sửa tiếp" kèm nút **Tải lại**;
  - mất mạng → giống các thao tác khác: xếp vào hàng đợi offline và gửi lại khi có mạng (dùng `client_action_id`).
- Trang `field/page.tsx` chỉ thêm 1–2 dòng để gắn component.

### 3.3 Mobile

Không thêm màn hình, vì kỹ thuật viên không được sửa. Mobile tự thấy thông tin mới ở lần tải ca tiếp theo, kể cả bản lưu offline.

### 3.4 Cơ sở dữ liệu

**Không cần migration.** Dùng các cột đã có; nhật ký dùng bảng sự kiện đã có.

## 4. File sẽ tạo / sửa

| File | Việc |
|---|---|
| `backend/field/schemas.py` | thêm `CaseDetailsUpdate` |
| `backend/field/router.py` | thêm endpoint `update_case_details` (khoảng 60 dòng) |
| `backend/field/integrations.py` | (nếu Q1 = gửi) thêm loại sự kiện `case.details_changed` vào outbox |
| `frontend/components/field/CaseDetailsEditor.tsx` | mới |
| `frontend/app/(app)/field/page.tsx` | gắn component (1–2 dòng) |
| `frontend/lib/api.ts` | kiểu dữ liệu nếu cần |
| `tests/test_field_case_details.py` | mới (mục 5) |
| `README.md`, `docs/PRD.md` | đánh dấu M14 đã làm |

## 5. Kiểm thử sẽ viết

Mỗi tiêu chí nghiệm thu ứng với ít nhất một test:
- Trưởng đội sửa 6 trường → giá trị mới được lưu và có đúng **1** dòng nhật ký, ghi đủ cũ → mới.
- Kỹ thuật viên gọi API → **403**; người ngoài đội → **404**.
- Gửi giá trị không đổi → không có dòng nhật ký nào.
- Dữ liệu sai (tiêu đề 1 ký tự, địa chỉ 600 ký tự) → **422**.
- Gửi lại cùng `client_action_id` → áp dụng đúng 1 lần.
- Xung đột (Q3): hai người sửa từ cùng một bản → người sau nhận **409**, dữ liệu của người trước còn nguyên.
- Đổi địa chỉ → toạ độ xử lý theo Q2. Ca đã hoàn tất → xử lý theo Q4.
- CRM gửi `ticket.updated` sau khi trưởng đội sửa → áp dụng như cũ, nhật ký ghi `external`.
- Toàn bộ suite cũ vẫn đạt (613 test); lint, build và client test đạt.

## 6. Ảnh hưởng tới phần đang chạy

- Không đổi API cũ, không đổi CSDL, không đổi quyền của các thao tác cũ.
- Rủi ro chính là **toạ độ cũ sai khi đổi địa chỉ**, ảnh hưởng tới lập lịch tuyến. Xem Q2.

## 7. Các câu hỏi cần chủ dự án chọn

| | Câu hỏi | **Đã chọn (2026-11-22)** |
|---|---|---|
| Q1 | Có báo CRM khi trưởng đội sửa thông tin ca không? | **Có, chỉ với ca đến từ CRM**: sự kiện mới `case.details_changed` kèm danh sách trường đã đổi, gửi qua hàng đợi gửi lại sẵn có |
| Q2 | Đổi địa chỉ thì toạ độ cũ xử lý thế nào? | **Xoá toạ độ cũ**: ca hiện "cần toạ độ", lập lịch bỏ qua ca đó cho tới khi nhập lại (M13 sẽ tự gợi ý). Nhật ký ghi "đã xoá toạ độ cũ do đổi địa chỉ". |
| Q3 | Hai người sửa cùng lúc thì sao? | **Báo xung đột**: `expected_updated_at` không khớp → **409**, không ghi đè |
| Q4 | Ca đã hoàn tất có sửa được không? | **Không**: ca `resolved` → **409** "Mở lại ca trước khi sửa thông tin" |
