# M15 — Thông báo đẩy cho lịch hôm nay: bản thiết kế

- **Trạng thái:** **ĐÃ CHỌN PHƯƠNG ÁN (2026-11-22)**: cả 4 câu ở mục 7 đều theo khuyến nghị. Chưa viết code, chờ lệnh "Bắt đầu code".
- **PRD:** `docs/PRD.md` §5.2, mục "Thông báo đẩy cho lịch hôm nay", ghi là làm sau M11 cơ bản.
- **Ước lượng:** khoảng 2,5 ngày gồm kiểm thử: máy chủ 1 ngày, mobile 1 ngày, bản build thử trên Android 0,5 ngày.

## 1. Mục tiêu (giải thích dễ hiểu)

Hiện nay (M11) thợ chỉ thấy lịch mới khi **tự mở app hoặc kéo để làm mới**. Nếu trưởng đội giao thêm ca gấp lúc 10 giờ, thợ có thể không biết cho tới khi nhìn lại điện thoại.

M15 làm cho **điện thoại thợ hiện thông báo** ngay cả khi app đang đóng. Ví dụ: "Bạn có 1 ca mới hôm nay — bắt đầu 10:30". Bấm vào thông báo thì app mở thẳng tab **Hôm nay**.

Tiêu chí nghiệm thu đề xuất:
1. Trưởng đội giao ca, xếp lịch hoặc đổi lịch của một thợ thì **trong vòng 1 phút** điện thoại thợ đó hiện thông báo, nếu máy có mạng.
2. Chỉ **người được giao** nhận thông báo; thành viên khác và người ngoài đội không nhận.
3. Lưu một lịch 10 ca cho một thợ thì thợ nhận **1 thông báo gộp**, không phải 10 thông báo.
4. Nội dung thông báo **không có tên, số điện thoại hay địa chỉ khách** (xem Q2).
5. Đăng xuất thì máy đó **không nhận thông báo nữa**. Thợ tắt quyền thông báo thì app vẫn chạy bình thường như M11.
6. Dịch vụ thông báo lỗi hoặc chưa cấu hình thì **việc giao ca và xếp lịch vẫn thành công**; thông báo chỉ là phần thêm.

## 2. Hiện trạng (đã đọc code)

- **Chưa có gì về thông báo đẩy**: app chưa có `expo-notifications`, máy chủ chưa lưu "địa chỉ nhận thông báo" của máy nào.
- **Chỗ giao ca:** `PATCH /field/cases/{id}` trong `backend/field/router.py`, khi đổi `assigned_to`.
- **Chỗ xếp lịch:** `POST /field/workspaces/{id}/schedule/apply` trong `backend/field/dispatch.py`. Hàm này ghi `assigned_to`, `scheduled_start`, `route_order` cho từng ca.
- **Báo ngược CRM** đã có hàng đợi gửi lại (`FieldWebhookDelivery` + `kick()` trong `backend/field/integrations.py`). M15 làm theo cùng kiểu: ghi vào hàng đợi trong cùng giao dịch, gửi ở luồng nền, lỗi thì thử lại.
- **App:** tab **Hôm nay** (M11) đã có, nên chỉ cần mở đúng tab khi bấm thông báo.
- **`app.json`** chưa có `projectId` của Expo. Muốn gửi thông báo đẩy qua Expo thì bắt buộc phải có (xem Q3).

## 3. Thiết kế

### 3.1 Đường đi của một thông báo

```
Trưởng đội lưu lịch
   → máy chủ ghi ca + ghi 1 dòng vào hàng đợi thông báo (cùng một lần lưu)
   → luồng nền gom theo từng thợ, gửi lên dịch vụ Expo Push (miễn phí)
   → Expo chuyển sang Google (Android) / Apple (iPhone)
   → điện thoại thợ hiện thông báo
```

### 3.2 Máy chủ

- **Bảng mới `field_push_devices`**: `user_id`, `token` (địa chỉ nhận của máy, dạng `ExponentPushToken[...]`, duy nhất), `platform`, `created_at`, `last_seen_at`. Một người có thể có nhiều máy.
- **API mới:**
  - `POST /field/push/devices`: app gửi token sau khi đăng nhập và được cấp quyền. Token đã thuộc người khác thì **chuyển sang người mới**, vì máy đã đổi chủ.
  - `DELETE /field/push/devices/{token}`: app gọi khi đăng xuất.
- **Bảng mới `field_push_outbox`** (hàng đợi): `user_id`, `kind` (`assigned` / `schedule_changed` / `unassigned`), `case_id`, `created_at`, `sent_at`, `attempts`.
  - Ghi ở 3 chỗ: giao ca, bỏ giao hoặc chuyển ca cho người khác, và lưu lịch.
  - Không ghi khi người giao ca **tự giao cho chính mình**.
- **Luồng gửi:**
  - Mỗi lần có việc mới, luồng gửi **chờ khoảng 20 giây** rồi gom các dòng chưa gửi theo từng người thành 1 thông báo. Nhờ vậy lưu lịch 10 ca chỉ thành 1 thông báo.
  - Gửi tới `https://exp.host/--/api/v2/push/send`, tối đa 100 thông báo mỗi lần gọi. Chỉ gửi token và nội dung ở mục 3.3.
  - Lỗi mạng hoặc lỗi 5xx thì thử lại tối đa 5 lần, giãn dần. Quá 24 giờ thì bỏ (lịch đã cũ).
  - Expo báo `DeviceNotRegistered` (gỡ app, tắt quyền) thì **xoá token đó**.
  - Lịch dọn dữ liệu hằng ngày xoá các dòng đã gửi quá 7 ngày.
- **Cấu hình:** `PUSH_ENABLED` (mặc định bật nếu có `EXPO_PROJECT_ID`), `EXPO_ACCESS_TOKEN` (không bắt buộc; Expo khuyên dùng để chống gửi giả).

### 3.3 Nội dung thông báo (theo Q2, đề xuất)

| Tình huống | Tiêu đề | Nội dung |
|---|---|---|
| Có ca mới hôm nay | FieldOps — lịch hôm nay | Bạn có 2 ca mới. Ca đầu bắt đầu 10:30. |
| Lịch đổi giờ hoặc thứ tự | FieldOps — lịch hôm nay | Lịch của bạn vừa được cập nhật (3 ca). |
| Ca chuyển cho người khác | FieldOps — lịch hôm nay | 1 ca đã được chuyển cho người khác. |
| Ca ngày khác | FieldOps — lịch ngày 24/11 | Bạn có 1 ca mới. |

Dữ liệu ẩn kèm theo chỉ gồm `{"screen": "today", "date": "2026-11-24"}`, không có mã ca hay thông tin khách. App tự tải chi tiết ca qua API như bình thường.

### 3.4 Mobile

- Thêm `expo-notifications` và `expo-device`.
- **Xin quyền đúng lúc:** sau khi đăng nhập, ở tab Hôm nay hiện thẻ "Bật thông báo để biết ngay khi có ca mới" với nút **Bật**. Không bật lên hộp xin quyền ngay khi mở app.
- Được quyền thì lấy token và gửi lên máy chủ. Đăng xuất thì gọi xoá token **trước** khi xoá phiên đăng nhập.
- Android: tạo kênh thông báo "Lịch làm việc" ở mức quan trọng cao, có âm thanh.
- **Bấm thông báo** thì mở tab Hôm nay đúng ngày và tự làm mới. Đang mở app mà có thông báo thì hiện băng nhỏ ở đầu màn hình và tự làm mới danh sách.
- **Bản web của Expo** và máy giả lập không nhận thông báo đẩy: thẻ "Bật thông báo" sẽ ẩn.

### 3.5 Web

Không đổi giao diện. Trưởng đội vẫn giao ca và xếp lịch như cũ; thông báo đi ngầm. Có thể thêm dòng nhỏ "Đã báo cho thợ qua điện thoại" trong nhật ký ca. Việc này để sau, nếu bạn muốn.

## 4. File sẽ tạo / sửa

| File | Việc |
|---|---|
| `backend/field/push.py` (mới) | Bảng thiết bị, hàng đợi, API đăng ký/xoá token, luồng gửi, gom thông báo, xử lý lỗi |
| `backend/field/models.py` | 2 bảng mới (tạo bằng `create_all`, không cần migration cột) |
| `backend/field/router.py`, `backend/field/dispatch.py` | Ghi hàng đợi khi giao ca, chuyển ca, lưu lịch |
| `backend/config.py`, `backend/main.py`, `backend/pipeline/jobs/tasks.py` | Cấu hình, gắn router, dọn hàng đợi cũ |
| `mobile/lib/push.ts` (mới), `mobile/lib/api.ts` | Xin quyền, lấy token, đăng ký/xoá, xử lý bấm thông báo |
| `mobile/screens/TodayScreen.tsx`, `mobile/navigation/RootNavigator.tsx` | Thẻ "Bật thông báo", mở đúng tab khi bấm |
| `mobile/app.json`, `mobile/package.json` | Plugin `expo-notifications`, `projectId` |
| `deploy/cloud/.env.example`, `docker-compose.yml`, README | Biến `EXPO_PROJECT_ID`, `EXPO_ACCESS_TOKEN`, hướng dẫn tạo tài khoản Expo |
| `tests/test_field_push.py` (mới), `tests/client_state.test.cjs` | Kiểm thử ở mục 5 |

## 5. Kiểm thử sẽ viết

Máy chủ (dùng dịch vụ Expo **giả**, không gửi ra ngoài):
- Đăng ký token; token chuyển chủ khi người khác đăng nhập cùng máy; đăng xuất thì xoá.
- Giao ca → đúng người nhận; tự giao cho mình → không gửi; người ngoài đội → không nhận.
- Lưu lịch 10 ca → 1 thông báo gộp mỗi thợ; chuyển ca → thợ cũ nhận "đã chuyển", thợ mới nhận "ca mới".
- Nội dung không chứa tên, số điện thoại, địa chỉ khách.
- Expo lỗi 5xx → thử lại; `DeviceNotRegistered` → xoá token; quá 24 giờ → bỏ.
- Dịch vụ tắt hoặc lỗi → giao ca và lưu lịch vẫn thành công.

App: đăng xuất gọi xoá token trước; dữ liệu kèm thông báo mở đúng tab và ngày; dữ liệu lạ bị bỏ qua.

## 6. Ảnh hưởng và rủi ro

- **Cần tài khoản Expo (miễn phí)** để có `projectId`. Android cần thêm một dự án Firebase miễn phí để Google chuyển thông báo. iPhone cần tài khoản Apple Developer **99 USD/năm** (xem Q3).
- **Không thử được bằng Expo Go hay bản web.** Từ SDK 53, Expo Go không nhận thông báo đẩy từ xa nữa. Muốn kiểm chứng thật phải **build một bản APK** (EAS Build có gói miễn phí) và cài lên **điện thoại Android thật**. Phần này cần bạn làm cùng, vì phải đăng nhập tài khoản Expo của bạn.
- **Dữ liệu cá nhân:** token và nội dung thông báo đi qua máy chủ Expo (Mỹ) và Google/Apple. Vì vậy nội dung chỉ có số ca và giờ (Q2). Việc này cần thêm vào phần rà soát pháp lý ở bước 7.
- **Thông báo không đảm bảo tới 100%:** máy tắt nguồn, chế độ tiết kiệm pin hoặc mất mạng có thể làm chậm hay mất thông báo. Lịch trong app (M11) vẫn là nguồn chính xác.
- **Không ảnh hưởng phần đang chạy:** thêm bảng mới, không đổi cột cũ. Thiếu cấu hình thì tính năng tự tắt.

## 7. Các câu hỏi cần chủ dự án chọn

- **Q1. Khi nào gửi thông báo?** Đề xuất: ca mới được giao, lịch đổi giờ hoặc thứ tự, ca bị chuyển cho người khác. Không gửi khi trưởng đội chỉ sửa ghi chú hoặc triệu chứng.
- **Q2. Nội dung có thông tin khách không?** Đề xuất: **không**, chỉ số ca và giờ bắt đầu. Thông báo hiện cả trên màn hình khoá và đi qua máy chủ bên thứ ba.
- **Q3. Nền tảng nào?** Đề xuất: **chỉ Android** ở giai đoạn 1 (0 đồng). iPhone cần 99 USD/năm, để giai đoạn 2.
- **Q4. Gộp thông báo?** Đề xuất: **gộp trong khoảng 20 giây cho mỗi thợ**. Mỗi thao tác của trưởng đội thì thợ nhận nhiều nhất 1 thông báo.

**Đã chọn (2026-11-22):**
- Q1: ca mới, đổi lịch, chuyển ca.
- Q2: không ghi thông tin khách, chỉ số ca và giờ.
- Q3: chỉ Android.
- Q4: gộp 1 thông báo mỗi 20 giây cho mỗi thợ.
