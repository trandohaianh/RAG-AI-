# Hệ thống 2: Vận hành hiện trường — ca sửa, ảnh, điều phối tuyến, tích hợp CRM/NMS

**Phạm vi mã nguồn:**

- `backend/field/router.py` (workspace, thành viên, ca, checklist, sự kiện, idempotency offline)
- `backend/field/photos.py`
- `backend/field/dispatch.py`
- `backend/field/integrations.py`
- `backend/field/models.py`
- Các phần liên quan: `auth/lifecycle.py` (khi tài khoản bị vô hiệu hoá) và `metrics.py` (gauge webhook)

---

## 1. Vai trò của hệ thống

Đội kỹ thuật làm việc trong **workspace**, với hai vai trò:

| Vai trò | Quyền |
|---|---|
| `owner` (trưởng đội) | Quản lý tài liệu, lập lịch, cấu hình tích hợp, phân công ca |
| `technician` (kỹ thuật viên) | Làm việc trên các ca được giao |

Mỗi **ca sửa** gồm checklist (step), nhật ký sự kiện, ảnh hiện trường, toạ độ và khung giờ hẹn. Ca có thể được tạo tay, hoặc tạo từ ticket CRM hay cảnh báo NMS. Khi trạng thái ca đổi, hệ thống gửi webhook ngược về CRM/NMS.

---

## 2. Luồng xử lý

### 2.1 Truy cập ca

- **Kiểm tra thành viên:** `_case_for_user` join bảng membership. Người ngoài đội luôn nhận 404, không phải 403, nên không lộ việc ca có tồn tại.
- **Phạm vi:** step, event, evidence và trợ lý đều đi qua hàm này, và còn lọc theo `case_id`.

### 2.2 Phân quyền sửa ca (D2, đã sửa)

`PATCH /field/cases/{id}` gọi `_require_case_edit_rights`:

- **Owner:** sửa được mọi trường.
- **Kỹ thuật viên:**
  - chỉ được **nhận** một ca chưa có người phụ trách cho chính mình, không được giao ca cho người khác hay bỏ giao ca của người khác;
  - chỉ được đổi trạng thái ca giao cho mình, hoặc ca chưa giao do chính mình tạo.

`PATCH /field/cases/{id}/dispatch` (vị trí, khung giờ, ưu tiên, thời lượng) giờ **chỉ dành cho owner**, nhất quán với plan/apply lịch. Trên web, khối chỉnh điều phối chỉ hiển thị với owner.

Lý do: mỗi lần giao ca sinh webhook `case.assigned` sang CRM, và mọi dữ liệu dispatch là đầu vào của bộ lập lịch.

### 2.3 Đồng bộ offline và idempotency (`_claim_client_action`)

Mỗi thao tác từ outbox mang theo `client_action_id`. Server xử lý như sau:

1. **Băm request:**

   ```
   hash = SHA-256(JSON chuẩn hoá {body: thân request bỏ client_action_id, target: {step_id}})
   ```

   Phần `target` có từ bản sửa này. Bản ghi cũ chỉ băm phần body, và vẫn được chấp nhận để replay.
2. **Đã có bản ghi khớp** (cùng case, actor, loại thao tác và hash): trả lại kết quả đã áp dụng (replay).
3. **Khác ở bất kỳ điểm nào:** trả 409.
4. **Hai lần gửi lại đồng thời** (đã sửa): lần thứ hai gặp `IntegrityError` khi `flush`. Code rollback, đọc lại bản ghi và trả replay, thay vì lỗi 500.

### 2.4 Ảnh hiện trường (`photos.py`)

1. Đọc tối đa `FIELD_PHOTO_MAX_MB + 1` byte.
2. Kiểm tra số ảnh của ca.
3. `process_image` chạy trong executor:
   - nhận dạng định dạng từ nội dung tệp (JPEG, PNG, WebP);
   - **chặn khi `width × height > 40 MP` trước khi giải nén** (F9). Pillow chỉ tự raise khi ảnh vượt gấp đôi giới hạn;
   - xoay theo EXIF, mã hoá lại thành JPEG (cạnh dài tối đa 2560 px, thumbnail 360 px), bỏ EXIF và GPS.
4. **Kiểm tra lại hạn mức dưới khoá ghi (U6, đã sửa):**
   1. `rollback` để kết thúc transaction chỉ-đọc.
   2. Chạy một `UPDATE` không đổi gì trên dòng workspace. Câu lệnh này lấy khoá ghi trên SQLite và khoá dòng trên PostgreSQL.
   3. Đếm lại số ảnh của ca và dung lượng ảnh của đội.
   4. Ghi tệp và commit.

   Nhờ đó các lượt upload song song (ví dụ outbox xả hàng đợi) không vượt được hạn mức.
5. **Truy cập ảnh:** qua URL ký `HMAC(photo_id, variant, exp)`, TTL 900 s, so sánh bằng `compare_digest`.

### 2.5 Điều phối tuyến (`dispatch.py`)

**Thời gian di chuyển ước lượng:**

$$travel = \left\lceil \frac{1{,}35 \cdot d_{haversine}}{v}\cdot 60 \right\rceil$$

với $v$ = 30 km/h. Ví dụ: 4 km đường chim bay thành 5,4 km, tức 10,8 phút, làm tròn lên **11 phút**.

**Khả thi:** $start = \max(arrive, earliest)$; tuyến bị loại nếu $start > latest$ hoặc $end > shift\_end$.

**Hàm mục tiêu:**

$$cost = \sum (travel + w_{prio}\cdot(start - shift\_start))$$

với $w$ = {urgent 0,5; high 0,2; normal 0,05; low 0}. Ví dụ: một ca khẩn chờ 60 phút "đắt" bằng 30 phút lái xe.

**Thuật toán:**

1. Sắp xếp ca theo ưu tiên, rồi theo `latest` tăng dần.
2. Chèn rẻ nhất, với điểm $\Delta cost + 3\cdot|seq|$ (hạng tử sau dùng để chia tải).
3. Cải thiện từng tuyến bằng 2-opt khi tuyến vẫn khả thi.

**Độ phức tạp:** $O(M^2 \cdot T \cdot L)$ lần mô phỏng (M ca, T kỹ thuật viên, L độ dài tuyến). Thuật toán là heuristic và không tối ưu toàn cục.

**Kiểm tra khi ghi lịch (đã sửa):**

- **F12:** null tường minh cho `priority` hay `est_minutes` trả 422 thay vì `IntegrityError` 500. Khung giờ được kiểm tra **sau khi gộp** với giá trị đã lưu.
- **F13:** `apply` từ chối ca đã đóng.
- **F14:** `tz_offset_minutes` phải nằm trong [−720, 840].

### 2.6 Tích hợp CRM/NMS

**Webhook vào** (`POST /public/field/integrations/{public_id}/events`):

1. **Kích thước (U10, đã sửa):** kiểm `Content-Length` rồi đọc body theo stream, dừng ngay khi vượt 256 KB. Trước đây body được đọc hết vào RAM rồi mới kiểm, mà endpoint này chỉ cần `public_id`, không cần chữ ký.
2. **Chữ ký:**

   ```
   sig = "sha256=" + HMAC_SHA256(secret, ts + "." + body)
   ```

   Lệch đồng hồ tối đa 300 s, so sánh bằng `compare_digest`.
3. **Dedupe** theo `event_id` (unique constraint).
4. **Tìm ca** theo `kind:id` và `external_ref`. Hai sự kiện khác `event_id` cùng tạo ca cho một `external_ref` thì bên đến sau nhận **503 có `Retry-After`** (đã sửa), thay vì 500. Sự kiện đó chưa được ghi nhận, nên khi CRM gửi lại sẽ đi nhánh cập nhật ca.

**Webhook ra:**

1. **Ghi hàng đợi:** `enqueue_case_event` ghi delivery trong cùng transaction với thay đổi của ca.
2. **Giành quyền gửi (U4, đã sửa):** `dispatch_due` claim từng delivery bằng

   ```sql
   UPDATE … SET next_attempt_at = now + 5 phút
   WHERE id = :id AND status = 'pending' AND next_attempt_at <= now
   ```

   rồi commit. Chỉ bên thấy `rowcount = 1` mới gửi. Container `pipeline` và `kick()` trong API không còn gửi trùng. Nếu tiến trình chết giữa chừng, delivery tự đến hạn lại sau 5 phút.
3. **Kiểm tra URL và ghim IP (F10, U5, đã sửa):**
   - `validate_outbound_url` chỉ nhận https và chỉ nhận IP `is_global`, nên chặn 10/8, 127/8, 169.254/16, 100.64/10, IPv4-mapped IPv6 và 0.0.0.0.
   - `_post_pinned` POST thẳng tới **IP vừa kiểm tra**. Header `Host` và SNI giữ tên miền gốc, nên chứng chỉ TLS vẫn được xác minh theo tên miền. Cách này chặn DNS rebinding.
   - Không theo redirect.
4. **Retry:** $delay(a) = \min(30\cdot 2^{a}, 21600)$ giây. Sau 8 lần thì chuyển sang `dead`. Ví dụ: a = 1 chờ 60 s, a = 3 chờ 240 s.
5. **Retry thủ công:** từ chối delivery đã `delivered` (409, U11), để không tạo bản trùng phía CRM.
6. **Giám sát (U9, đã sửa):** API xuất gauge `fieldops_field_webhook_deliveries{status}` đọc từ DB. Alert `FieldOpsWebhookDeadLetter` dùng `delta(...{status="dead"}[1h]) > 0`. Counter cũ chỉ tăng trong container pipeline, mà container đó không được scrape.

---

## 3. Lỗi đã sửa

| ID | Mức | Vị trí | Kịch bản lỗi trước khi sửa | Test |
|---|---|---|---|---|
| D2 | TB–Cao | `router.py:update_case`, `dispatch.py:update_case_dispatch` | Kỹ thuật viên gán lại ca của người khác, đặt mọi ca thành `urgent`, và kích hoạt webhook `case.assigned` sang CRM. | `round2::test_technician_cannot_reassign_or_redispatch_cases_of_others` |
| F9 | TB | `photos.py:process_image` | Một PNG 79 MP nặng dưới 10 MB bị giải nén hoàn toàn (vài trăm MB RAM mỗi request). | `test_photo_between_one_and_two_times_the_pixel_cap_is_rejected` |
| U6 | Thấp–TB | `photos.py:upload_photo` | Upload song song vượt giới hạn số ảnh mỗi ca và dung lượng của đội. | `round2::test_photo_limit_is_rechecked_after_slow_processing` |
| F10 | TB | `integrations.py:validate_outbound_url` | URL webhook trỏ được vào 100.64.0.0/10 (CGNAT, metadata của một số cloud). | `test_outbound_webhook_rejects_shared_address_space` |
| U5 | TB | `integrations.py:dispatch_due` | DNS rebinding: kiểm tra thấy IP công khai, rồi lúc kết nối lại tới 127.0.0.1. | `round2::test_outbound_request_is_pinned_to_the_checked_ip` |
| U4 | TB–Thấp | `integrations.py:dispatch_due` | Hai tiến trình cùng POST một delivery. | `round2::test_two_dispatchers_never_send_the_same_delivery_twice` |
| U10a | Thấp | `router.py:_claim_client_action` | Dùng lại một mã thao tác cho bước khác thì bản sửa bị bỏ qua mà không báo lỗi ("đã áp dụng"). | `round2::test_replayed_action_id_for_another_checklist_step_is_a_conflict` |
| U10b | Thấp | `router.py:_claim_client_action` | Hai lần gửi lại đồng thời: một bên nhận 500. | `round2::test_concurrent_retry_with_the_same_action_id_replays_instead_of_500` |
| U10d | Thấp | `integrations.py:inbound_event` | Body nhiều MB được đọc hết vào RAM trước khi kiểm tra kích thước. | `round2::test_inbound_webhook_rejects_oversized_body_before_reading_it` |
| U10c | Thấp | `integrations.py:inbound_event` | Race khi tạo ca theo `external_ref` trả 500. | Kiểm bằng đọc code (503 có `Retry-After`) |
| U11 | Thấp | `integrations.py:retry_delivery` | Gửi lại một sự kiện đã giao thành công. | `round2::test_delivered_webhook_cannot_be_retried` |
| U9 | Thấp–TB | `metrics.py`, `monitoring/alerts` | Alert dead-letter gần như không bao giờ kích hoạt. | `round2::test_dead_letter_gauge_is_exported_by_the_api` |
| F11 | Thấp–TB | `auth/lifecycle.py` | Ca CRM mới được giao cho người đã rời đội. | `test_deactivated_member_is_removed_as_integration_default_assignee` |
| F12–F14 | Thấp | `dispatch.py` | Lỗi 500 khi null tường minh hoặc khi tz quá biên; khung giờ đảo ngược được lưu; ca đã đóng vẫn được xếp lịch. | `test_dispatch_patch_rejects_…`, `test_schedule_rejects_closed_cases_…` |

---

## 4. Hạn chế còn lại

- **Vị trí checklist (đã sửa ở vòng 3):** trước đây `create_step` dùng `len(case.steps)`, và hai lần thêm bước đồng thời trùng vị trí. Giờ code chạy một `UPDATE` không đổi gì trên dòng ca để lấy khoá (khoá ghi trên SQLite, khoá dòng trên PostgreSQL), rồi đặt `position = MAX(position) + 1`. Test: `round3::test_concurrently_added_checklist_steps_get_distinct_positions` (FAIL trên `7f0955a`).
- **Bản ghi idempotency cũ** (trước bản sửa) vẫn khớp theo hash chỉ gồm body, để không biến các lượt replay hợp lệ thành 409 sau khi nâng cấp.
- **Lập lịch:** thời gian di chuyển là ước lượng đường chim bay × 1,35, không phải dẫn đường thật.
- **Ghim IP:** chỉ áp dụng khi `FIELD_WEBHOOK_ALLOW_PRIVATE` tắt. Bật biến này (môi trường dev) thì hệ thống tin DNS.

---

## 5. Vùng đã xác minh là đúng

- Người được giao ca và `default_assignee` phải là thành viên của đội.
- Chỉ owner thêm được thành viên, và người mới luôn vào với vai trò `technician`. Không có endpoint đổi vai trò.
- Không vô hiệu hoá được owner cuối cùng của một đội khi đội vẫn còn thành viên khác.
- Xoá ảnh chỉ dành cho người upload hoặc owner. Storage key là UUID do server sinh, nên không có path traversal.
- Webhook vào dedupe theo `event_id` và chịu rate-limit theo từng tích hợp.

---

## 6. Kiểm thử

| Bộ test | Kết quả |
|---|---|
| `tests/test_field_operations.py`, `test_field_dispatch_photos_integrations.py` | PASS sau khi sửa |
| Các test tương ứng trong `test_audit_regressions*.py` | Đều FAIL trên HEAD và PASS sau khi sửa |
| Alert rules | YAML parse được (12 rule) |

`promtool` không chạy được cục bộ; CI có bước `promtool check rules`.
