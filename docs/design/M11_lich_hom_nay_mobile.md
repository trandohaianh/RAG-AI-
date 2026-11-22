# M11 — Lịch hôm nay trên mobile: bản thiết kế

- **Trạng thái:** **ĐÃ DUYỆT (2026-11-22)**, với lựa chọn của chủ dự án ở mục 7. Chưa viết code.
- **PRD:** `docs/PRD.md` §5.1 M11.
- **Ước lượng:** khoảng 1,5 ngày, gồm cả kiểm thử.

## 1. Mục tiêu (giải thích dễ hiểu)

Hiện nay trưởng đội xếp lịch tuyến trên **web**, nhưng app của kỹ thuật viên **không có màn lịch**. Thợ phải mở web hoặc hỏi lại trưởng đội xem hôm nay đi ca nào trước.

M11 thêm màn **"Hôm nay"** trong app. Thợ mở app là thấy ngay các ca được giao cho mình trong ngày, đúng thứ tự tuyến và giờ dự kiến. Màn này **vẫn xem được khi mất sóng**.

Tiêu chí nghiệm thu (PRD):
1. Thấy danh sách ca **được giao cho mình trong ngày**, đúng thứ tự tuyến và giờ dự kiến.
2. Xem được khi offline, dùng dữ liệu của lần đồng bộ gần nhất và có ghi rõ "cập nhật lúc …".
3. Trưởng đội đổi lịch thì app thấy lịch mới ở lần mở màn hoặc kéo-làm-mới tiếp theo.

Ngoài phạm vi M11: thông báo đẩy. PRD đã để sau, làm khi M11 cơ bản chạy ổn.

## 2. Hiện trạng (đã đọc code)

- **Lịch được lưu ngay trên ca:** trưởng đội áp dụng lịch thì mỗi ca có `assigned_to`, `scheduled_start` (giờ bắt đầu dự kiến) và `route_order` (thứ tự trong tuyến). Xem `backend/field/dispatch.py`, `apply_schedule`.
- **API đọc lịch** `GET /field/workspaces/{id}/schedule?date=…` trả lịch của **cả đội**. API này cho **mọi thành viên** gọi, nên kỹ thuật viên xem được tuyến và **địa chỉ khách của người khác** (xem Q2).
- **App mobile** có 5 tab: Tổng quan, Ca sửa, Tri thức, Đội, Cài đặt. Màn chi tiết ca đã có sẵn và làm việc offline được.
- **Dữ liệu offline** lưu mã hoá trong SecureStore (Keychain/Keystore), theo từng người dùng, qua `mobile/lib/fieldOffline.ts`. Logic này có test trong `tests/client_state.test.cjs`.
- Một kỹ thuật viên có thể thuộc **nhiều đội**.

## 3. Thiết kế

### 3.1 API mới: "lịch của tôi"

`GET /field/my-schedule?date=YYYY-MM-DD&tz_offset_minutes=420`

- Trả **chỉ các ca giao cho người đang đăng nhập**, gộp từ **mọi đội** người đó thuộc về. Đây là quyền tối thiểu: thợ chỉ thấy ca của mình.
- Mỗi ca gồm:
  - `case_id`, đội, tiêu đề, trạng thái, ưu tiên;
  - địa chỉ, khách hàng, model;
  - giờ dự kiến, thứ tự tuyến, khung giờ hẹn khách, thời gian xử lý dự kiến;
  - toạ độ, để mở bản đồ.
- Sắp xếp theo giờ dự kiến, rồi theo thứ tự tuyến.
- Theo Q4, có thêm nhóm **"được giao nhưng chưa xếp lịch"**.
- Ca đã hoàn tất vẫn hiện trong ngày, gạch mờ, để thợ thấy tiến độ: "3/5 ca xong".
- Không cần đổi CSDL; chỉ đọc các cột đã có.

### 3.2 App mobile

- **Màn mới `mobile/screens/TodayScreen.tsx`**, thành **tab đầu tiên "Hôm nay"** (Q1).
- **Khối "Lịch hôm nay" trên màn Tổng quan** (Q1, chủ dự án chọn có cả hai): hiện tiến độ "x/y ca xong" và **2–3 ca tiếp theo** (giờ, tiêu đề, địa chỉ). Bấm vào khối thì mở tab Hôm nay. Khối này dùng cùng dữ liệu, kể cả bản offline, nên không gọi API lần thứ hai.
  - Đầu màn: ngày, kèm số ca xong / tổng số ca.
  - Mỗi dòng: giờ dự kiến, thứ tự, tiêu đề, địa chỉ, nhãn ưu tiên (khẩn màu đỏ), trạng thái.
  - Bấm vào một ca thì mở **màn chi tiết ca đã có** (checklist, ảnh, ghi chú…).
  - Nút **"Chỉ đường"** mở ứng dụng bản đồ của máy theo toạ độ, rồi thợ tự dẫn đường.
  - Kéo xuống để làm mới.
  - Chuyển **Hôm nay / Ngày mai** theo Q3.
- **Offline:** mỗi lần tải thành công thì lưu lịch vào bộ nhớ an toàn, theo người dùng và theo ngày. Khi không có mạng:
  - hiện bản đã lưu, kèm dòng **"Đang offline — lịch cập nhật lúc 07:42"**;
  - chưa từng tải lịch thì báo "Chưa có lịch trong máy, cần kết nối mạng một lần".
- **Dọn dữ liệu:** chỉ giữ lịch của hôm nay và ngày mai. Đăng xuất thì xoá cùng các dữ liệu offline khác của người dùng, theo cơ chế đã có.

### 3.3 Web

Không đổi giao diện web.

## 4. File sẽ tạo / sửa

| File | Việc |
|---|---|
| `backend/field/dispatch.py` | thêm `GET /field/my-schedule`; theo Q2, giới hạn API lịch cả đội |
| `mobile/lib/api.ts` | hàm `getMySchedule(date)` và kiểu dữ liệu |
| `mobile/lib/fieldOffline.ts` | `cacheMySchedule` / `getCachedMySchedule` (dùng lại cơ chế lưu mã hoá sẵn có) |
| `mobile/screens/TodayScreen.tsx` | mới |
| `mobile/navigation/RootNavigator.tsx` | thêm tab "Hôm nay"; mở chi tiết ca từ tab này |
| `mobile/screens/FieldHomeScreen.tsx` | thêm khối "Lịch hôm nay" (tóm tắt), bấm thì mở tab Hôm nay |
| `tests/test_field_my_schedule.py` | mới (backend) |
| `tests/client_state.test.cjs` | thêm test lưu/đọc lịch offline |
| `docs/PRD.md`, `README.md` | đánh dấu M11 đã làm |

## 5. Kiểm thử sẽ viết

- Kỹ thuật viên chỉ nhận ca **của mình**, không thấy ca của người khác trong cùng đội. Người thuộc 2 đội thấy ca của cả 2 đội.
- Đúng thứ tự (giờ dự kiến, thứ tự tuyến). Ca ngày khác không lọt vào. Ranh giới ngày theo giờ Việt Nam (+7) đúng, ví dụ ca lúc 23:30 và 00:15.
- Nhóm "chưa xếp lịch" (theo Q4); ca đã hoàn tất vẫn hiện, có cờ trạng thái.
- Theo Q2: kỹ thuật viên gọi API lịch cả đội → chỉ nhận tuyến của mình (hoặc 403). Trưởng đội vẫn thấy cả đội.
- Offline (client test): lưu rồi đọc lại đúng người dùng; người khác đăng nhập trên cùng máy **không** đọc được; lịch quá hạn bị dọn.
- Toàn bộ suite cũ đạt; mobile `tsc` đạt.

**Giới hạn kiểm chứng:** máy này không có điện thoại hay trình giả lập Android/iOS. Mình sẽ kiểm bằng `tsc`, test logic và chạy app ở chế độ web của Expo trên trình duyệt (nếu chạy được). Cảm giác trên điện thoại thật cần bạn thử lại.

## 6. Ảnh hưởng tới phần đang chạy

- Thêm API mới, không đổi CSDL.
- Theo Q2, API lịch cả đội **đổi hành vi với kỹ thuật viên**. Màn "Điều phối tuyến" trên web chỉ trưởng đội dùng, nên không ảnh hưởng tới họ.
- Thêm một tab vào app; các tab cũ giữ nguyên.

## 7. Các câu hỏi cần chủ dự án chọn

| | Câu hỏi | **Đã chọn (2026-11-22)** |
|---|---|---|
| Q1 | Đặt lịch ở đâu trong app? | **Cả hai:** tab mới "Hôm nay" (đứng đầu, đầy đủ) **và** khối tóm tắt trên màn Tổng quan |
| Q2 | Kỹ thuật viên có được xem lịch và địa chỉ khách của **cả đội** không? | **Không:** API lịch cả đội chỉ trả tuyến của chính họ; trưởng đội vẫn xem cả đội |
| Q3 | Xem được những ngày nào? | **Hôm nay + ngày mai** |
| Q4 | Ca giao cho tôi nhưng chưa xếp lịch có hiện không? | **Có**, ở nhóm riêng "Được giao, chưa xếp lịch" |
