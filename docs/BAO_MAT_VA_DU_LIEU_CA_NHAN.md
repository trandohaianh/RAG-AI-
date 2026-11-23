# FieldOps — Bảo mật và dữ liệu cá nhân (bước 7)

- **Ngày:** 2026-11-23. Áp dụng cho nhánh `fullstack/fieldops-production`, MVP M1–M15.
- **Trạng thái:** đã rà code và đối chiếu khung pháp lý. **Chưa phải ý kiến pháp lý:** phần §4 cần luật sư hoặc người phụ trách pháp chế xác nhận trước khi dùng với khách thật.

## 1. Danh sách kiểm tra bảo mật (đã có trong code)

| # | Hạng mục | Trạng thái | Bằng chứng |
|---|---|---|---|
| 1 | Không có khoá bí mật trong mã nguồn | ✅ | `.env`, `deploy/production/.env`, `deploy/production/secrets/` bị `.gitignore` chặn; chỉ commit `.env.example` |
| 2 | Production từ chối khởi động khi cấu hình nguy hiểm | ✅ | `validate_security_config()` trong `backend/config.py` |
| 3 | Mật khẩu băm, token có hạn, đăng xuất thu hồi token | ✅ | `backend/auth/service.py` (`token_version`, `purpose`), M1 |
| 4 | Chặn dò mật khẩu (HTTP 429), người lạ không khoá được tài khoản người khác | ✅ | `backend/auth/ratelimit.py`, M1 |
| 5 | Chống CSRF khi dùng cookie | ✅ | `backend/dependencies.py` bắt header `X-Requested-With: fieldops` cho phương thức ghi |
| 6 | Header bảo mật, không lộ chi tiết lỗi | ✅ | `backend/security_headers.py`, trang lỗi tiếng Việt |
| 7 | Phân quyền theo đội: người ngoài đội nhận 404; thợ không sửa thông tin ca hay giao ca cho người khác | ✅ | `_case_for_user`, `_require_case_edit_rights` trong `backend/field/router.py`; test quyền của M3, M12–M15 |
| 8 | Ảnh hiện trường: xoá GPS/EXIF, chỉ xem qua link có hạn 15 phút, lưu ngoài thư mục web | ✅ | `backend/field/photos.py`, M8 |
| 9 | Webhook CRM: chữ ký HMAC + dấu thời gian, chống gửi lặp; gọi ra ngoài có chặn SSRF | ✅ | `sign()`, `validate_outbound_url()` trong `backend/field/integrations.py`, M9 |
| 10 | Thao tác offline gửi lại chỉ áp dụng đúng 1 lần | ✅ | `_claim_client_action`, M4–M5 |
| 11 | Trần chi phí AI theo ngày | ✅ | `backend/costs/budget.py`, M10 |
| 12 | Gửi ra bên ngoài tối thiểu: Nominatim chỉ nhận địa chỉ; thông báo đẩy chỉ có số ca và giờ | ✅ | M13 (`geocode.py`), M15 (`push.py`, có test kiểm tra không lộ tên khách) |
| 13 | Hạn lưu giữ: ảnh và chữ ký của ca đã xong xoá sau 365 ngày; nhật ký tích hợp 30–90 ngày; cache địa chỉ 90 ngày; hàng đợi thông báo 7 ngày | ✅ | job `retention` trong `backend/pipeline/jobs/tasks.py` |
| 14 | Sao lưu hằng ngày và **đã thử khôi phục** | ✅ (máy phát triển) | job `backup`, công cụ `python -m pipeline.jobs.restore` (bước 8) |
| 15 | Lỗ hổng thư viện | ⚠ | Web: `npm audit` 0 lỗ hổng. Mobile: 9 cảnh báo (3 high: `node-forge`, `zod` trong công cụ build Expo, không chạy trên máy chủ). Backend: xem `docs/DEPENDENCY-SECURITY.md` |

## 2. Dữ liệu cá nhân đang xử lý

| Dữ liệu | Của ai | Lưu ở đâu | Ai xem được | Hạn lưu |
|---|---|---|---|---|
| Email, mật khẩu đã băm | Thành viên đội | CSDL (SQLite trên VM) | Chính người đó; admin hệ thống | Đến khi người đó tự xoá tài khoản (Cài đặt → Xoá tài khoản) |
| Tên/nhãn khách, địa chỉ, toạ độ, model và số serial thiết bị, triệu chứng | Khách hàng của đội | CSDL | Thành viên của đội đó | Chưa có hạn (ca được giữ để tra lịch sử) |
| Ảnh hiện trường, **chữ ký khách** (M12) | Khách hàng | Thư mục ảnh trên VM, đã xoá EXIF/GPS | Thành viên đội, qua link 15 phút | 365 ngày sau khi ca xong |
| Vị trí GPS của thợ khi bấm "Tôi đang ở nhà khách" (M13) | Thợ, gián tiếp là khách | Chỉ lưu thành toạ độ của ca | Thành viên đội | Như ca |
| Mã nhận thông báo của máy (M15) | Thợ | CSDL | Không ai xem trên giao diện | Xoá khi đăng xuất hoặc gỡ app |
| Câu hỏi gửi trợ lý AI, tài liệu SOP | Thành viên đội | CSDL, Chroma | Thành viên đội | Hội thoại: chưa có hạn. Nhật ký chi phí AI: 400 ngày (`USAGE_EVENT_RETENTION_DAYS`) |

## 3. Bên thứ ba nhận dữ liệu (có chuyển ra nước ngoài)

| Bên nhận | Nhận gì | Ở đâu | Có thể tắt? |
|---|---|---|---|
| Oracle Cloud (VM) | Toàn bộ dữ liệu, vì máy chủ đặt ở đây | Theo vùng bạn chọn khi tạo VM | Không. Chọn vùng gần Việt Nam (Singapore) |
| Google Gemini (gói trả phí) | Câu hỏi và đoạn tài liệu SOP liên quan | Mỹ / toàn cầu | Có: tắt trợ lý AI |
| OpenStreetMap Nominatim (M13) | Chỉ chuỗi địa chỉ khách | Châu Âu | Có: để trống `NOMINATIM_CONTACT` |
| Expo + Google FCM (M15) | Mã nhận thông báo của máy, số ca, giờ | Mỹ | Có: `PUSH_ENABLED=0` |
| Google Sign-In (nếu bật) | Email người đăng nhập | Mỹ | Có |
| Grafana Cloud, GitHub Actions | Chỉ số vận hành, không có dữ liệu khách | Mỹ / EU | Có |

## 4. Đối chiếu pháp luật Việt Nam (cần luật sư xác nhận)

Khung hiện hành:
- **Luật Bảo vệ dữ liệu cá nhân số 91/2025/QH15**, có hiệu lực từ 01/01/2026.
- **Nghị định 356/2025/NĐ-CP** hướng dẫn thi hành Luật, có hiệu lực cùng ngày và **thay thế Nghị định 13/2023**.

Những nghĩa vụ liên quan trực tiếp tới FieldOps, và tình trạng hiện tại:

| Nghĩa vụ | Tình trạng FieldOps |
|---|---|
| Thông báo cho người dùng: thu thập gì, để làm gì, gửi cho ai, lưu bao lâu | ✅ Trang `/privacy` (web) và màn "Chính sách quyền riêng tư" (app), phiên bản 2026-11-23. ⚠ Nội dung cần luật sư duyệt; cần điền liên hệ (`NEXT_PUBLIC_PRIVACY_CONTACT`, `EXPO_PUBLIC_PRIVACY_CONTACT`) |
| Sự đồng ý phải được lưu lại và kiểm chứng được thời điểm, nội dung (Nghị định 356 siết chặt điểm này) | ✅ Đăng ký bắt buộc tick đồng ý; mỗi lần đồng ý ghi một dòng `user_consents` (phiên bản, nguồn, thời điểm). Tài khoản cũ, đăng nhập Google, hoặc khi chính sách đổi phiên bản thì phải đồng ý mới dùng tiếp |
| Quyền của chủ thể dữ liệu: xem, sửa, xoá, rút đồng ý | ✅ Cài đặt → "Tải dữ liệu của tôi" (JSON) và "Xoá tài khoản" (web + app). Xoá: ẩn danh email, thu hồi mọi phiên, xoá hội thoại, tài liệu cá nhân, máy nhận thông báo, nhật ký đồng ý; xoá hẳn đội chỉ có một mình người đó (kèm ca, ảnh, chữ ký); trưởng đội duy nhất của đội còn thành viên phải chuyển quyền trước |
| Dữ liệu khách hàng của đội | ⚠ Đội là bên thu thập dữ liệu khách. Cần thông báo cho khách, ví dụ trong hợp đồng dịch vụ hoặc phiếu biên bản, và cần chốt vai trò "bên kiểm soát / bên xử lý" giữa đội và FieldOps |
| Hồ sơ đánh giá tác động xử lý dữ liệu (DPIA) | ❌ Chưa lập. Nghị định 356 có biểu mẫu mới |
| Hồ sơ đánh giá tác động chuyển dữ liệu ra nước ngoài (CTIA) | ❌ Chưa lập, trong khi có chuyển dữ liệu sang Gemini, Nominatim, Expo/Google (§3) |
| Thông báo khi lộ lọt dữ liệu | ⚠ Có cảnh báo vận hành (Grafana), nhưng chưa có quy trình "ai báo ai, trong bao lâu" |
| Bảo vệ kỹ thuật | ✅ Phần lớn đã có (§1). Còn thiếu: sao lưu ra ngoài VM, chưa mã hoá riêng ở mức ứng dụng (chỉ dựa vào mã hoá ổ đĩa của Oracle; cần kiểm tra trên VM) |

Doanh nghiệp nhỏ và doanh nghiệp khởi nghiệp **có thể được ân hạn một số nghĩa vụ trong 5 năm** theo Điều 41 Nghị định 356; hộ kinh doanh và doanh nghiệp siêu nhỏ thì không. Đội của bạn thuộc loại nào và được miễn nghĩa vụ nào cần luật sư xác nhận. Các nguồn tổng hợp mô tả chi tiết chưa thống nhất, nên luôn đối chiếu văn bản gốc trên vbpl.moj.gov.vn.

Nguồn đã đối chiếu:
- Thư viện Pháp luật: Luật 91/2025/QH15 và Nghị định 356/2025/NĐ-CP.
- Bản tin pháp lý của PwC Việt Nam (28/01/2026) và EY Việt Nam (tháng 3/2026) về Nghị định 356.

## 5. Rủi ro còn lại và đề xuất (cần chủ dự án chọn)

| Ưu tiên | Việc | Loại | Ước lượng |
|---|---|---|---|
| ✅ P1 | Trang "Chính sách quyền riêng tư" (web + app), dẫn từ màn đăng ký; **lưu thời điểm và phiên bản** điều khoản mỗi người đã đồng ý — *đã làm 2026-11-23; nội dung còn chờ luật sư duyệt* | Code + nội dung | — |
| ✅ P1 | "Xoá tài khoản" và "Tải dữ liệu của tôi" trong Cài đặt — *đã làm 2026-11-23* | Code | — |
| P1 | Sao lưu **ra ngoài VM** (hiện bản sao lưu nằm cùng ổ với dữ liệu: mất VM là mất cả hai). Ví dụ `rclone` lên Google Drive hoặc Oracle Object Storage miễn phí, kèm mã hoá | Cấu hình VM | 0,5 ngày |
| P2 | Hạn lưu giữ cho **ca đã xong** (ví dụ ẩn danh tên, địa chỉ khách sau N tháng) | Code + quyết định của bạn | 0,5 ngày |
| P2 | Lập hồ sơ DPIA và CTIA theo biểu mẫu Nghị định 356 | Tài liệu, bạn hoặc luật sư | — |
| P2 | Quy trình xử lý sự cố lộ lọt dữ liệu (1 trang) | Tài liệu | 0,5 ngày |
| P3 | Cập nhật bản vá Expo để hết cảnh báo `node-forge` / `zod` ở mobile (chỉ ảnh hưởng công cụ build) | Bảo trì | 0,5 ngày |
