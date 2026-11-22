# M13 — Địa chỉ → toạ độ tự động: bản thiết kế

- **Trạng thái:** **ĐÃ CHỌN PHƯƠNG ÁN (2026-11-22)** — 6 câu ở mục 7 đã có lựa chọn. Chưa viết code, chờ lệnh "Bắt đầu code".
- **PRD:** `docs/PRD.md` §5.1 M13.
- **Ước lượng:** khoảng 3,5 ngày gồm kiểm thử (web 2 ngày + mobile 1,5 ngày, theo Q1).

## 1. Mục tiêu (giải thích dễ hiểu)

Bảng điều phối xếp tuyến dựa vào **toạ độ** của khách. Hiện nay toạ độ phải **nhập tay**: dán từ Google Maps hoặc bấm "vị trí hiện tại". Ca không có toạ độ thì **không được xếp tuyến**. Thêm vào đó, từ M14, sửa địa chỉ sẽ xoá toạ độ cũ.

M13 thêm nút **"Tìm toạ độ"**: hệ thống tra địa chỉ trên OpenStreetMap (dịch vụ Nominatim, miễn phí) và đặt một **ghim trên bản đồ**. Trưởng đội xem ghim có đúng nhà khách không, kéo ghim nếu lệch, rồi mới lưu. Không tìm được thì báo rõ và vẫn nhập tay như cũ.

Tiêu chí nghiệm thu (PRD):
1. Nhập địa chỉ khi tạo hoặc sửa ca thì có gợi ý toạ độ.
2. Trưởng đội xác nhận hoặc sửa trên bản đồ **trước khi lưu**.
3. Không tìm được thì báo rõ và vẫn cho nhập tay.
4. **0 đồng:** tuân thủ giới hạn ≤ 1 yêu cầu/giây của Nominatim và có cache.
5. Chỉ gửi địa chỉ đi; **không gửi tên hay số điện thoại khách**.

## 2. Hiện trạng (đã đọc code)

- **Toạ độ ca:** cột `latitude` / `longitude` của `field_service_cases`, sửa qua `PATCH /field/cases/{id}/dispatch` (`backend/field/dispatch.py`). Phải có đủ cả hai giá trị.
- **Web:** ô "Toạ độ khách hàng" trong `frontend/components/field/CaseDispatchEditor.tsx` nhận dạng `21.0285, 105.8542`, có nút "vị trí hiện tại" và link chỉ đường.
- **Bản đồ có sẵn:** web đã dùng **Leaflet** và ảnh nền OpenStreetMap trong `RouteMap.tsx` (bảng điều phối). Vì vậy không cần thêm thư viện bản đồ.
- **Sửa địa chỉ (M14)** xoá toạ độ cũ và ghi "cần nhập lại toạ độ".
- **CRM** gửi ca kèm toạ độ nếu có. Không có thì ca nằm ở nhóm "chưa có vị trí" trên bảng điều phối.
- **Máy chủ chạy 1 tiến trình** (`deploy/cloud/docker-compose.yml`, `--workers 1`). Nhờ vậy, giữ nhịp ≤ 1 yêu cầu/giây chỉ cần một khoá trong tiến trình, không cần Redis.
- `httpx` đã có trong `backend/requirements.txt`.

## 3. Thiết kế

### 3.1 Gọi Nominatim **từ máy chủ**, không từ trình duyệt

Trình duyệt gọi `POST /field/workspaces/{ws}/geocode` với `{ "address": "..." }`. Máy chủ sẽ:
1. **Chuẩn hoá địa chỉ:** bỏ khoảng trắng thừa, chữ thường, thêm "Việt Nam" nếu thiếu. Giới hạn 5–300 ký tự.
2. **Tra cache** (3.2). Có rồi thì trả ngay, không gọi ra ngoài.
3. Chưa có thì gọi `https://nominatim.openstreetmap.org/search`:
   - tham số `q=<địa chỉ>`, `countrycodes=vn`, `format=jsonv2`, `addressdetails=1`, `limit=3`, `accept-language=vi`;
   - **User-Agent** nhận diện ứng dụng và có liên hệ (Q4), đúng chính sách của Nominatim;
   - qua một **khoá toàn tiến trình**: hai lần gọi liền nhau cách nhau ≥ 1,1 giây; người thứ hai phải chờ, quá 10 giây thì báo "đang bận, thử lại";
   - timeout 8 giây.
4. Trả về tối đa 3 gợi ý, mỗi gợi ý gồm `lat`, `lng`, tên hiển thị và **mức chính xác**: số nhà / đường / phường-xã / tỉnh. Mức chính xác suy từ `addresstype` của Nominatim.

**Tại sao làm ở máy chủ:**
- chỉ máy chủ giữ được nhịp 1 yêu cầu/giây chung cho mọi người dùng, và giữ được cache chung;
- trình duyệt không lộ IP người dùng ra ngoài;
- không phải nới CSP `connect-src`;
- máy chủ là nơi **duy nhất** quyết định gửi gì ra ngoài: chỉ chuỗi địa chỉ, không có tên khách, mã ca hay số máy.

**Chính sách Nominatim mà thiết kế phải theo:**
- ≤ 1 yêu cầu/giây;
- User-Agent rõ ràng;
- **cấm gợi ý tự động theo từng phím gõ** (autocomplete);
- phải cache kết quả;
- ghi công OpenStreetMap.

Vì vậy chỉ gọi khi người dùng **bấm nút**, không gọi theo từng ký tự.

### 3.2 Cache — bảng mới `field_geocode_cache`

| Cột | Nghĩa |
|---|---|
| `id` | khoá |
| `workspace_id` | cache **theo đội** (Q3) |
| `query_hash` | SHA-256 của địa chỉ đã chuẩn hoá. Chỉ lưu hash, không lưu địa chỉ gốc lần nữa |
| `results_json` | tối đa 3 gợi ý đã rút gọn: lat, lng, tên hiển thị, mức chính xác |
| `created_at` | dùng cho hạn 90 ngày; quá hạn thì tra lại |

Kết quả "không tìm thấy" cũng được cache, nhưng chỉ 1 ngày. Như vậy bấm lại liên tục không gọi ra ngoài, mà OSM có cập nhật thì vẫn thấy.

Đây là **bảng mới** nên `create_all` tự tạo, không cần migration (như M12). Dọn bản quá hạn chạy chung lịch dọn ảnh hết hạn.

### 3.3 Web — hộp "Xác nhận vị trí"

Có 2 chỗ có nút **"Tìm toạ độ"**:
- trong `CaseDispatchEditor` (chi tiết ca), dùng địa chỉ hiện tại của ca;
- sau khi **tạo ca** có địa chỉ, và sau khi **sửa địa chỉ** (M14 đã xoá toạ độ cũ): hiện lời nhắc "Ca chưa có toạ độ — Tìm toạ độ".

Bấm nút sẽ mở **hộp thoại bản đồ** (Leaflet, dùng chung ảnh nền với `RouteMap`):
- Có gợi ý thì đặt **ghim kéo được** ở gợi ý đầu, kèm danh sách 2–3 gợi ý để chọn và **nhãn mức chính xác**. Ví dụ: "⚠ Chỉ chính xác đến phường, hãy kéo ghim tới đúng nhà".
- Không có gợi ý thì báo "Không tìm thấy địa chỉ này trên OpenStreetMap". Bản đồ mở ở trung tâm khu vực của đội để **bấm chọn hoặc kéo ghim bằng tay**, và vẫn còn ô dán toạ độ như cũ.
- Nút **"Lưu vị trí này"** gọi `PATCH /field/cases/{id}/dispatch` có sẵn. Nhật ký ca ghi "Đặt toạ độ (tìm từ địa chỉ, đã xác nhận trên bản đồ)" hoặc "(nhập tay)".
- Có dòng ghi công "© OpenStreetMap contributors" theo yêu cầu giấy phép.

**Không** tự lưu toạ độ khi chưa có người xác nhận (tiêu chí 2). Lý do: Nominatim ở Việt Nam thường **thiếu số nhà**, nhiều khi chỉ ra được tên đường hoặc phường.

### 3.4 Ca từ CRM không kèm toạ độ — Q2

Nếu chọn phương án khuyến nghị (tra sẵn, chờ xác nhận):
- Khi CRM tạo ca hoặc đổi địa chỉ mà không gửi toạ độ, máy chủ xếp một việc "tra toạ độ" **chạy nền**. Việc này đi chung nhịp 1 yêu cầu/giây và cache ở 3.1.
- Kết quả được lưu vào cột mới `location_suggestion_json` của ca (migration 8). Cột này **không** dùng để xếp tuyến.
- Trên web, ca hiện nhãn "Có gợi ý vị trí — xác nhận". Bấm vào là mở hộp bản đồ ở 3.3 với ghim đặt sẵn.
- Lưu toạ độ, hoặc đổi địa chỉ, thì gợi ý bị xoá.

### 3.5 Mobile (Q1 + Q5 + Q6)

Ở chi tiết ca trên điện thoại có khối **"Vị trí khách"**:
- **Chưa có toạ độ:** hiện nút **"Tìm toạ độ"** và nút **"Tôi đang ở nhà khách"**.
  - "Tôi đang ở nhà khách" lấy GPS của máy qua `expo-location` (miễn phí). Đây là cách chính xác nhất khi thợ đã đến nơi.
- **Đã có toạ độ:** chỉ hiện "Chỉ đường" như M11. Thêm link nhỏ **"Vị trí sai? Gửi vị trí đúng"**.

**Bản đồ (Q6):** màn `LocationPickerScreen` dùng `react-native-webview` chạy một trang Leaflet nhỏ nằm sẵn trong app:
- cùng ảnh nền OpenStreetMap và cùng ghim kéo được như web;
- trang bản đồ báo toạ độ ghim về app qua `postMessage`;
- không cần khoá API.

**Quyền (Q5):**
- **Trưởng đội** dùng mobile thì **lưu thẳng**, như trên web (`PATCH /dispatch`).
- **Kỹ thuật viên được giao ca** thì **gửi gợi ý**: `POST /field/cases/{id}/location-suggestion` với `{lat, lng, source: "gps"|"geocoded"|"manual"}`.
  - Gợi ý được lưu vào cùng cột `location_suggestion_json` như Q2, kèm người gửi.
  - Nhật ký ghi "KTV gửi vị trí đề xuất (GPS tại nhà khách)".
  - Trưởng đội thấy nhãn "Có gợi ý vị trí — xác nhận" trên web (hoặc mobile), bấm vào là mở bản đồ với ghim đặt sẵn.
  - Gợi ý **không** dùng để xếp tuyến cho đến khi trưởng đội xác nhận.
- Người khác (không được giao, không phải trưởng đội): 403.

**Mất sóng:**
- "Tìm toạ độ" cần mạng, nên báo "cần mạng để tìm".
- "Tôi đang ở nhà khách" (GPS) **vẫn dùng được offline**. Gợi ý xếp vào hàng đợi an toàn sẵn có, kiểu thao tác mới `location_suggest`, và gửi khi có mạng (giống M12).

**Quyền riêng tư:**
- App chỉ xin quyền vị trí **lúc bấm nút**, không theo dõi nền.
- Toạ độ GPS của thợ chỉ được gửi khi thợ bấm "Gửi". Nó trở thành vị trí đề xuất của **khách**, không lưu thành lịch sử di chuyển của thợ.

## 4. File sẽ tạo / sửa

| File | Việc |
|---|---|
| `backend/field/geocode.py` *(mới)* | API `/geocode`, chuẩn hoá địa chỉ, cache, khoá nhịp 1 yêu cầu/giây, gọi Nominatim, suy mức chính xác |
| `backend/field/models.py` | bảng `FieldGeocodeCache` |
| `backend/config.py` | `GEOCODER_ENABLED`, `NOMINATIM_URL`, `NOMINATIM_CONTACT` (Q4), hạn cache |
| `backend/main.py` | đăng ký router |
| `backend/field/integrations.py`, `backend/schema_migrations.py` | *chỉ nếu Q2 chọn tra sẵn:* xếp việc tra nền và thêm cột `location_suggestion_json` (migration 8) |
| `backend/field/dispatch.py` | nhận thêm `location_source: "geocoded" \| "manual"` (tuỳ chọn) để ghi nhật ký đúng |
| `frontend/components/field/LocationPicker.tsx` *(mới)* | hộp thoại bản đồ, ghim kéo được, danh sách gợi ý, nhãn chính xác |
| `frontend/components/field/CaseDispatchEditor.tsx` | nút "Tìm toạ độ" mở `LocationPicker` |
| `frontend/app/(app)/field/page.tsx` | lời nhắc "chưa có toạ độ" sau khi tạo hoặc sửa địa chỉ; nhãn "Có gợi ý vị trí — xác nhận" |
| `backend/field/geocode.py` | thêm `POST /field/cases/{id}/location-suggestion` (Q5) |
| `mobile/package.json` | thêm `expo-location`, `react-native-webview` (qua `npx expo install`) |
| `mobile/assets/map.html` *(mới)* | trang Leaflet nhỏ cho WebView, nhận/gửi toạ độ ghim qua `postMessage` |
| `mobile/screens/LocationPickerScreen.tsx` *(mới)* | bản đồ, danh sách gợi ý, nút GPS, lưu / gửi gợi ý |
| `mobile/screens/FieldCaseDetailScreen.tsx` | khối "Vị trí khách" |
| `mobile/lib/api.ts`, `mobile/lib/fieldOffline.ts` | API geocode / suggestion; thao tác offline `location_suggest` |
| `mobile/app.json` | lời xin quyền vị trí bằng tiếng Việt (iOS) |
| `.env.example`, `docs/` | biến cấu hình mới, hướng dẫn chủ dự án |

## 5. Kiểm thử sẽ viết

Test **không bao giờ gọi Nominatim thật**: dùng `httpx.MockTransport` giả lập câu trả lời.

| Tiêu chí | Test |
|---|---|
| Tìm được thì trả gợi ý kèm mức chính xác | có kết quả số nhà / đường / phường → nhãn đúng |
| Không tìm được | trả danh sách rỗng và thông báo rõ, không lỗi 500 |
| Cache | tra 2 lần cùng địa chỉ (khác hoa-thường, khoảng trắng) → chỉ 1 lần gọi ra ngoài; quá hạn → gọi lại |
| Nhịp ≤ 1 yêu cầu/giây | 3 lần tra khác địa chỉ liên tiếp → khoảng cách giữa các lần gọi ≥ 1,1 giây (đồng hồ giả) |
| Chỉ gửi địa chỉ | kiểm tra URL gửi đi: có `q=` địa chỉ, **không** có tên khách hay mã ca; có User-Agent |
| Dịch vụ lỗi / quá chậm / trả 429 | báo "không tra được, hãy nhập tay", không cache lỗi tạm thời |
| Quyền | chỉ thành viên đội dùng được; người ngoài → 404; tắt `GEOCODER_ENABLED` → 503 kèm thông báo |
| Lưu toạ độ ghi nhật ký nguồn | `location_source=geocoded` → nhật ký ghi "tìm từ địa chỉ, đã xác nhận" |
| Q2 tra sẵn cho ca CRM | ca CRM không toạ độ → có gợi ý, **không** vào xếp tuyến; xác nhận → có toạ độ, gợi ý bị xoá; CRM đổi địa chỉ → gợi ý cũ bị xoá |
| Q3 cache theo đội | đội B tra cùng địa chỉ với đội A → vẫn gọi ra ngoài (không dùng cache của A) |
| Q5 quyền gợi ý | KTV được giao gửi được; KTV khác → 403; người ngoài → 404; gợi ý không làm đổi `latitude` |
| Q5 offline | client test: gợi ý GPS xếp hàng rồi gửi đúng 1 lần (cùng `client_action_id`) |

Chạy thử thật trên trình duyệt (dữ liệu tạm): tìm 3–5 địa chỉ Hà Nội / TP.HCM thật (địa chỉ công cộng, không phải của khách), kéo ghim, lưu, rồi xem ca vào bảng điều phối.

## 6. Ảnh hưởng tới phần đang chạy

- Không đổi dữ liệu cũ, không đổi API cũ. `location_source` là tuỳ chọn.
- Có **một kết nối ra ngoài mới** (nominatim.openstreetmap.org). Nếu dịch vụ hỏng hoặc chặn, chỉ nút "Tìm toạ độ" báo lỗi; nhập tay vẫn chạy.
- **Dữ liệu cá nhân:** địa chỉ khách được gửi cho một bên thứ ba (Quỹ OpenStreetMap, máy chủ ở EU). Cần ghi vào phần rà soát pháp lý (PRD mục "⚠ Dữ liệu cá nhân", bước 7) và chính sách bảo mật.
- **Rủi ro độ chính xác ở Việt Nam:**
  - OSM thiếu nhiều số nhà, nên thường chỉ chính xác đến đường hoặc phường;
  - từ 7/2025 Việt Nam sắp xếp lại đơn vị hành chính (bỏ cấp huyện, gộp tỉnh), nên địa chỉ kiểu mới hoặc cũ có thể không khớp OSM.
  - Vì vậy **luôn bắt xác nhận trên bản đồ**.

## 7. Các câu hỏi cần chủ dự án chọn

**Đã chọn (2026-11-22):** Q1 **Web + mobile** · Q2 **Tra sẵn, chờ xác nhận** · Q3 **Riêng từng đội** · Q4 **Tự điền khi triển khai** · Q5 **KTV gửi gợi ý, trưởng đội duyệt** · Q6 **OpenStreetMap qua WebView**.

| | Câu hỏi | Phương án |
|---|---|---|
| Q1 | Dùng ở đâu? | **(Khuyến nghị) Chỉ web, cho trưởng đội**: nút "Tìm toạ độ" ở chi tiết ca, kèm lời nhắc sau khi tạo hoặc sửa địa chỉ · *Hoặc* thêm cả mobile cho kỹ thuật viên. Mobile cần bản đồ riêng (thư viện mới), thêm khoảng 1,5 ngày |
| Q2 | Ca từ CRM không kèm toạ độ? | **(Khuyến nghị) Tự tra sẵn, chờ xác nhận**: máy chủ tra nền khi ca vào và lưu thành *gợi ý* (không dùng xếp tuyến). Trưởng đội thấy nhãn "Có gợi ý vị trí — xác nhận" và bấm một lần · *Hoặc* không tự tra, trưởng đội tự bấm "Tìm toạ độ" như ca tạo tay · *Hoặc* tự tra và **dùng ngay** để xếp tuyến (nhanh nhưng có thể sai nhà) |
| Q3 | Cache chung hay theo đội? | **(Khuyến nghị) Theo đội**: địa chỉ khách của đội này không bị suy ra từ đội khác. Giai đoạn 2 (SaaS) cần tách bạch như vậy · *Hoặc* chung toàn hệ thống: ít gọi ra ngoài hơn |
| Q4 | Liên hệ trong User-Agent (Nominatim bắt buộc) | **(Khuyến nghị) Bạn tự điền** một email hoặc trang liên hệ vào biến `NOMINATIM_CONTACT` khi triển khai; để trống thì nút "Tìm toạ độ" **tắt** và chỉ nhập tay · *Hoặc* dùng địa chỉ trang web của dự án làm liên hệ |
| Q5 | Kỹ thuật viên trên mobile được làm gì với toạ độ? | **Gửi gợi ý, trưởng đội duyệt** *(đã chọn)* · Được lưu thẳng · Chỉ trưởng đội |
| Q6 | Bản đồ trên mobile | **OpenStreetMap qua WebView** *(đã chọn)* · react-native-maps (cần khoá Google) · Không có bản đồ |
