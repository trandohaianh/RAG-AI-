# Báo cáo theo từng hệ thống — FieldOps

Đợt kiểm toán 2026-11-16 chia mã nguồn thành 7 hệ thống. Mỗi báo cáo trình bày:

- vai trò của hệ thống;
- luồng xử lý thực tế, kèm vị trí trong code;
- công thức và ví dụ tính (nếu có thuật toán);
- lỗi đã sửa, kèm regression test;
- hạn chế còn lại;
- vùng đã xác minh là đúng;
- kết quả kiểm thử.

| # | Hệ thống | Tệp | Lỗi đã sửa |
|---|---|---|---|
| 1 | Xác thực, tài khoản, billing, cổng embed và API | [01_Xac_thuc_Tai_khoan_Billing.md](01_Xac_thuc_Tai_khoan_Billing.md) | D1, F3, F4, F11, U2, U12, U13, U16b, U19, email không phân biệt hoa/thường, **V2** (cookie httpOnly, CSRF) |
| 2 | Vận hành hiện trường: ca, ảnh, điều phối, CRM/NMS | [02_Van_hanh_Hien_truong.md](02_Van_hanh_Hien_truong.md) | D2, F9–F14, U4, U5, U6, U9, U10, U11, **V5** (vị trí checklist) |
| 3 | RAG và trợ lý AI, chi phí, đánh giá | [03_RAG_AI_Tro_ly.md](03_RAG_AI_Tro_ly.md) | D3, F5–F8, F15–F17, U7, U14, U17, **V1** (trần ngân sách cứng), **V3** (system instruction) |
| 4 | Pipeline dữ liệu: medallion, job, Airflow | [04_Pipeline_Du_lieu.md](04_Pipeline_Du_lieu.md) | F18, F19, U8, U11b, U15 |
| 5 | Triển khai, giám sát, CI | [05_Trien_khai_Giam_sat_CI.md](05_Trien_khai_Giam_sat_CI.md) | F1, F2, D7, U9, U18b |
| 6 | Web frontend | [06_Web_Frontend.md](06_Web_Frontend.md) | D6, U16, F6 (UI), D2 (UI), F20, **V2** (cookie, CSP có nonce) |
| 7 | Ứng dụng mobile | [07_Ung_dung_Mobile.md](07_Ung_dung_Mobile.md) | U1, D6, U18, F6 (UI), **V4** (khôi phục máy bị bản cũ khoá nhầm) |

**Quy ước mã lỗi:**

- `F*`: lỗi sửa ở vòng 1.
- `U*`: lỗi đã xác nhận ở vòng 1 nhưng để lại, rồi sửa ở vòng 2.
- `D*`: hành vi trước đây được giữ để chủ dự án quyết định, nay đã được đổi.
- `D4` và `D5`: được **giữ nguyên** có chủ đích (xem báo cáo hệ thống 1 và 3).
- `V*`: vòng 3, xử lý dứt điểm năm hạn chế mà vòng 2 mới chỉ giảm thiểu.

Báo cáo tổng hợp:

- [../01_Technical_Code_Audit_Report.md](../01_Technical_Code_Audit_Report.md): phạm vi, bảng lỗi tổng, số liệu test trước và sau khi sửa, lộ trình.
- [../02_Deep_Technical_Explanation_Report.md](../02_Deep_Technical_Explanation_Report.md): giải thích kỹ thuật xuyên suốt các hệ thống.
