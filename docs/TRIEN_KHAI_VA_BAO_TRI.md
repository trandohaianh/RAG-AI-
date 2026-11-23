# FieldOps — Triển khai (bước 9) và bảo trì (bước 10)

- **Ngày:** 2026-11-23.
- **Trạng thái:** bước 9 **chưa triển khai thật**. Phần chuẩn bị đã xong; các việc ở §1 cần chủ dự án làm vì dùng tài khoản của bạn (Oracle, DuckDNS, Google, Expo).
- Hướng dẫn chi tiết từng lệnh: `deploy/cloud/README.md`.

## 1. Danh sách việc trước khi triển khai

| # | Việc | Ai làm | Trạng thái |
|---|---|---|---|
| 1 | Toàn bộ test đạt: backend, client, build web, tsc mobile | Claude | ✅ Đạt 2026-11-23 |
| 2 | Thử khôi phục bản sao lưu | Claude | ✅ Diễn tập trên máy phát triển (`docs/HIEU_NANG_VA_KHOI_PHUC.md`) |
| 3 | Đo hiệu năng theo mục tiêu đã chốt | Claude | ✅ Đạt ở kịch bản thực tế (p95 83 ms). Đã sửa lỗi treo 30 giây khi nhiều người dùng cùng lúc. Trợ lý AI chưa đo (`docs/HIEU_NANG_VA_KHOI_PHUC.md`) |
| 4 | Tạo VM Oracle Always Free (ARM, 2 OCPU / 12 GB), vùng gần Việt Nam | Chủ dự án | ⬜ |
| 5 | Tên miền DuckDNS trỏ về IP của VM | Chủ dự án | ⬜ |
| 6 | Điền `deploy/cloud/.env` trên VM: `SECRET_KEY`, `APP_DOMAIN`, `GEMINI_API_KEY`, `LLM_DAILY_BUDGET_USD`, `NOMINATIM_CONTACT`… Không gửi file này cho ai, không commit | Chủ dự án | ⬜ |
| 7 | **Chọn nhánh deploy:** `deploy.sh` mặc định lấy `origin/main`. Hoặc gộp `fullstack/fieldops-production` vào `main`, hoặc đặt `DEPLOY_BRANCH=fullstack/fieldops-production` | Chủ dự án quyết | ⬜ |
| 8 | Chạy `setup-vm.sh` hai lần (cài Docker, rồi build + chạy + smoke test 6 dòng ✓) | Chủ dự án, Claude hướng dẫn | ⬜ |
| 9 | Sao lưu ra ngoài VM (`rclone` lên Google Drive hoặc Object Storage, có mã hoá) | Chủ dự án | ⬜ |
| 10 | Monitoring Grafana Cloud và uptime | Chủ dự án | ⬜ |
| 11 | Bản APK Android có thông báo đẩy (Expo + Firebase) | Chủ dự án | ⬜ |
| 12 | Các việc pháp lý P1 trong `docs/BAO_MAT_VA_DU_LIEU_CA_NHAN.md` §5, **trước khi** nhập dữ liệu khách thật | Chủ dự án quyết; Claude code | 🟡 Chính sách, lưu đồng ý, tải dữ liệu, xoá tài khoản đã code. Còn: luật sư duyệt nội dung, điền liên hệ, sao lưu ra ngoài VM |

## 2. Kiểm tra sau khi triển khai

1. `bash smoke.sh https://<APP_DOMAIN>` in đủ 6 dòng ✓.
2. Đăng ký tài khoản trưởng đội, tạo đội, mời 1 thợ.
3. Tạo 1 ca có địa chỉ thật, bấm "Tìm toạ độ" và kiểm tra ghim trên bản đồ.
4. Xếp lịch, rồi mở app trên điện thoại thợ: thấy ca ở tab Hôm nay và nhận thông báo đẩy.
5. Thợ chụp ảnh, ký biên bản đóng ca; trên web thấy biên bản.
6. Hỏi trợ lý một câu có trong SOP: câu trả lời có nhãn nguồn 【E#】.
7. Hôm sau kiểm tra có bản sao lưu mới, và chạy `restore --latest --dry-run` để chắc đọc được.

## 3. Rollback

- `deploy.sh` sao lưu trước, giữ image cũ ở tag `:previous`, và tự quay về nếu smoke test lỗi.
- Nếu bản mới đã đổi CSDL: khôi phục bằng `python -m pipeline.jobs.restore` (README, mục "Khôi phục từ bản sao lưu").

## 4. Kế hoạch bảo trì (bước 10)

**Hằng ngày (tự động):** pipeline 02:00 UTC chạy KPI, kiểm tra chất lượng dữ liệu, dọn dữ liệu quá hạn và sao lưu. Uptime mỗi giờ. Cảnh báo khi 5xx > 5%, p95 > 3 giây, chi phí AI vượt mức, backup trễ hơn 26 giờ, ổ đĩa sắp đầy.

**Hằng tuần (chủ dự án, khoảng 10 phút):**
- Xem trang Chi phí và cảnh báo Grafana.
- Xem các webhook CRM bị "dead" và gửi lại nếu cần.
- Kiểm tra bản sao lưu đã được chép ra ngoài VM.

**Hằng tháng:**
- Cập nhật bản vá thư viện (`npm audit`, `pip-audit`), chạy lại toàn bộ test rồi deploy.
- Diễn tập khôi phục bằng `restore --latest --dry-run` trên VM.

**Hằng quý:** thử khôi phục đầy đủ trên một VM tạm; rà lại danh sách bên thứ ba (`docs/BAO_MAT_VA_DU_LIEU_CA_NHAN.md` §3) và chính sách quyền riêng tư.

**Danh sách cải tiến theo ưu tiên:**

| Ưu tiên | Việc | Lý do |
|---|---|---|
| P1 | Luật sư duyệt nội dung chính sách; điền liên hệ; sao lưu ra ngoài VM | Bắt buộc trước khi dùng dữ liệu khách thật (phần code P1 đã xong 2026-11-23) |
| P1 | Thử M11–M15 trên điện thoại Android thật | Chưa có tính năng mobile nào được thử trên máy thật |
| P2 | Hạn lưu giữ cho ca đã xong (ẩn danh khách sau N tháng) | Giảm dữ liệu cá nhân lưu lâu |
| P2 | Danh sách ca nhẹ hơn (trả tóm tắt, phân trang, gzip) | Khi cả đội cùng mở danh sách 200 ca trong 1 giây, người mở sau chờ 2–3 giây |
| P2 | Cảnh báo ca sắp trễ (SLA) | PRD §5.2, cần bạn cho mốc thời gian |
| P2 | Bản vá Expo để hết cảnh báo `node-forge` / `zod` | Bảo trì thư viện |
| P3 | iPhone (cần Apple Developer 99 USD/năm) | Giai đoạn 2 |
| P3 | Chuyển sang PostgreSQL khi có hơn 1 đội hoặc cần nhiều tiến trình | SQLite + 1 worker đủ cho < 50 người dùng |
