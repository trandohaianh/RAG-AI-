# SOP/manual mẫu FieldOps

Bộ giả lập để thử RAG: 8 file TXT, 60 câu hỏi ứng viên (48 có nguồn và 12 không có thông tin trả lời). Chưa có người phê duyệt chuyên môn; không dùng như quy trình hãng hoặc hướng dẫn sửa thiết bị thật.

- `01`–`08`: tiếp nhận, bằng chứng, ONU/router giả lập, offline, chuyển cấp, bàn giao và phiên bản tài liệu.
- `questions.template.json`: query, nguồn dự kiến và đáp án tham khảo; `kind=template` ngăn chạy live như dữ liệu đã duyệt.
- `annotations.draft.json`: 48 ca dev, 12 ca holdout, tất cả chưa duyệt. ID user/collection là ví dụ, phải sửa đúng phạm vi sử dụng.
- `lexical-benchmark.json`: fixture BM25 offline trên nội dung các TXT. Kết quả không đo vector, rerank, câu trả lời model hoặc an toàn thực tế. Một số câu thiếu thông tin vẫn có từ trùng trong tài liệu; không coi có kết quả truy xuất là đã có câu trả lời.

Để thử trong ứng dụng, tải 8 TXT vào Tài liệu của đội ở workspace được phép dùng. Việc embedding theo cấu hình có thể gọi API; bộ offline dưới đây không ingest hoặc gọi model.

```powershell
.\backend\.pytest_security_runtime_tmp\.venv\Scripts\python.exe -B backend/scripts/run_eval_benchmark.py --offline --dataset examples/fieldops-sop-demo/lexical-benchmark.json --output evaluation.local/fieldops-demo.lexical.json
```

Để bắt đầu gán nhãn, tạo bản làm việc trong thư mục riêng, đọc từng SOP rồi sửa câu hỏi/nguồn/đáp án và target. Dùng `backend/scripts/rag_review.py approve-case` để ghi nhận người chấm; export dev và holdout riêng. Không tự đánh dấu đã duyệt các nhãn do bộ mẫu tạo sẵn. Hướng dẫn đầy đủ tại [RAG-EVALUATION-GUIDE.md](../../docs/RAG-EVALUATION-GUIDE.md).

`60-cau-hoi-xem-nhanh.csv` dùng để đọc nhanh trong bảng tính. Bản phê duyệt chính là annotations.draft.json hoặc workbook riêng do CLI tạo; CSV không tự đồng bộ vào workflow.
