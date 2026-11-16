# Fixture kỹ thuật tổng hợp

Các chuỗi E01/E1, 504/5040, dòng điện/điện áp ở đây chỉ kiểm thử phần mềm. Không phải
SOP, thông số nhà sản xuất hay chỉ dẫn thao tác. Không dùng để sửa thiết bị.

`layout.unreviewed.json` chưa duyệt, build sẽ từ chối. Để thử quy trình duyệt trên
fixture này: đọc file gốc, đặt `scope_reviewed=true` và `single_equipment=true` trên
các block của bản JSON làm việc, giữ đúng section/row offsets, rồi chạy lệnh approve.
Không tự động áp dụng thao tác duyệt này cho tài liệu khách hàng.

`policy.example.json` chỉ là mẫu schema, min_score=0.4 tùy ý cho demo. Phải hiệu chỉnh
lại trên dev/holdout có người gán nhãn, gắn đúng model revision và calibration ID
trước khi sử dụng thật. Không gọi score là xác suất câu trả lời đúng.

Hướng dẫn và mã đầy đủ: `docs/TECHNICAL-RAG.md`, `backend/rag/technical/`.
