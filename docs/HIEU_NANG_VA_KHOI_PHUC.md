# FieldOps — Hiệu năng và khôi phục sao lưu (bước 8)

- **Ngày đo:** 2026-11-23, trên máy phát triển (WSL), **không phải VM Oracle**. Kết quả trên VM cần đo lại sau khi triển khai (§3).
- **Mục tiêu đã chốt (chủ dự án chọn "Mức đề xuất"):**
  - 50 người dùng cùng lúc;
  - 95% yêu cầu API thường (danh sách ca, chi tiết ca, lịch hôm nay) dưới 1 giây;
  - xếp tuyến 50 ca dưới 1 giây;
  - trợ lý AI trả lời dưới 15 giây;
  - không có lỗi 5xx.

## 1. Hiệu năng

**Cách đo** (script trong thư mục nháp, không nằm trong repo):
- Máy chủ chạy giống bản deploy: `uvicorn main:app --workers 1`, SQLite, bị giới hạn **2 nhân CPU** (`taskset`) cho gần với VM 2 OCPU.
- Dữ liệu: 50 người dùng trong 1 đội, 200 ca (150 ca giao rải cho thợ, 50 ca có toạ độ), 10 thợ có điểm xuất phát.
- Mỗi người dùng ảo lần lượt mở danh sách ca (200 ca), chi tiết một ca, rồi lịch hôm nay.

**Lỗi tìm được và đã sửa:**
- Lần đo đầu, **mọi yêu cầu treo đúng 30 giây** và máy chủ không tự dừng được.
- Nguyên nhân: hàm kiểm tra đăng nhập `get_current_user_allow_unverified` (`backend/dependencies.py`) là `async` nhưng đọc CSDL kiểu đồng bộ, nên chạy ngay trên luồng chính (event loop).
  - Khi hết kết nối trong pool, luồng chính đứng chờ một kết nối.
  - Các yêu cầu khác chỉ trả kết nối lại được qua chính luồng đó, nên tất cả kẹt tới khi hết hạn chờ 30 giây.
- **Lỗi này có trong bản đang chạy:** chỉ cần khoảng 15 yêu cầu đến cùng lúc là máy chủ treo.
- Sửa: đổi thành hàm thường (FastAPI chạy nó trong threadpool). Thêm test chống tái phát `tests/test_perf_regressions.py`.

**Kết quả sau khi sửa** (p95 = 95% yêu cầu nhanh hơn con số này):

| Kịch bản | Yêu cầu/giây | Danh sách ca p95 | Chi tiết ca p95 | Lịch hôm nay p95 | Tất cả p95 | Lỗi | Đạt? |
|---|---|---|---|---|---|---|---|
| **Thực tế:** 50 người, mỗi người thao tác khoảng 5 giây/lần, bắt đầu lệch nhau | 9,3 | 189 ms | 17 ms | 26 ms | **83 ms** | 0 | ✅ |
| 50 người **cùng bắt đầu một lúc**, 5 giây/lần | 9,4 | 3.359 ms | 14 ms | 11 ms | 827 ms | 0 | ⚠ danh sách ca chậm khi cả đội cùng mở một lúc |
| **Quá tải:** 50 người, mỗi người 1 giây/lần (50 yêu cầu/giây) | 25,1 (tối đa) | 3.051 ms | 1.372 ms | 1.084 ms | 2.641 ms | 0 | ❌ vượt sức 1 tiến trình, nhưng không lỗi, không treo |
| Xếp tuyến 50 ca, 10 thợ | — | — | — | — | 0,06 giây | 0 | ✅ (mục tiêu < 1 giây) |
| Trợ lý AI | — | — | — | — | **chưa đo** | — | Cần `GEMINI_API_KEY` thật; không dùng `.env` của bạn |

Không tải, danh sách 200 ca mất 30 ms; chi tiết ca 14 ms.

**Ý nghĩa:**
- Với cách dùng bình thường của một đội 50 người, máy chủ đáp ứng dư mục tiêu.
- Giới hạn hiện tại là khoảng **25 yêu cầu/giây**, vì 1 tiến trình Python với SQLite.
- Danh sách ca là phần nặng nhất (trả đủ 200 ca kèm bước và nhật ký, khoảng 148 KB). Nếu cả đội cùng mở danh sách trong cùng một giây, thì người mở sau phải chờ 2–3 giây.
- Đề xuất cải tiến (chưa làm): danh sách chỉ trả thông tin tóm tắt, phân trang, hoặc nén gzip. Ghi ở `docs/TRIEN_KHAI_VA_BAO_TRI.md`.

## 2. Thử khôi phục bản sao lưu

**Công cụ mới:** `python -m pipeline.jobs.restore` (`backend/pipeline/jobs/restore.py`).
- `--list`: liệt kê các bộ sao lưu. `--dry-run`: chỉ kiểm tra. `--latest --yes` / `--stamp ...`: khôi phục.
- Kiểm tra CSDL (`integrity_check`, có bảng `users`) và từng tệp trong tệp nén (không `..`, không link) **trước** khi đụng vào dữ liệu.
- Dữ liệu đang dùng không bị xoá, mà được chuyển sang `pre-restore-<thời điểm>/`.
- Lệnh trên VM: `deploy/cloud/README.md`, mục "Khôi phục từ bản sao lưu".

**Test tự động** (`tests/test_backup_restore.py`, 4 test):
- sao lưu, gây sự cố (mất người dùng, ảnh hỏng, thêm ảnh lạ), khôi phục: dữ liệu về đúng như lúc sao lưu, bản cũ còn giữ;
- chọn bộ mới nhất, bắt buộc `--yes`;
- CSDL hỏng thì từ chối, không đụng dữ liệu;
- tệp nén có đường dẫn `../` thì từ chối.

**Diễn tập với ứng dụng thật** (SQLite tạm, bản sao lưu để ở thư mục riêng như "ngoài VM"):
1. Đăng ký trưởng đội, tạo ca, tải ảnh hiện trường lên (201), chạy job `backup`: có `app-*.db`, `chroma-*.tar.gz`, `photos-*.tar.gz`.
2. **Sự cố:** xoá `app.db` và toàn bộ thư mục ảnh.
3. `restore --list`, `restore --latest --dry-run` (CSDL 1 người dùng, 1 ca, 2 tệp ảnh), `restore --latest --yes`.
4. Khởi động lại ứng dụng: đăng nhập được (200), ca "Ca diễn tập" còn nguyên, tải ảnh qua link có hạn được (200, `image/jpeg`).

**Kết luận:** sao lưu khôi phục được, trên máy phát triển.

## 3. Còn phải làm trên VM thật

- Đo lại kịch bản "Thực tế" trên VM Oracle (ARM) sau khi triển khai.
- Chạy `restore --latest --dry-run` trên VM; thử khôi phục đầy đủ trên một VM tạm mỗi quý.
- Đo thời gian trả lời của trợ lý AI với key thật (mục tiêu < 15 giây).
- Sao lưu ra ngoài VM: bản sao lưu hiện vẫn nằm cùng ổ với dữ liệu.
