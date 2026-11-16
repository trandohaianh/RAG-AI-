# Gán nhãn, chấm RAG và đối chiếu bằng chứng

## Bộ SOP/manual mẫu

`examples/fieldops-sop-demo/` có 8 TXT và 60 câu hỏi ứng viên: 48 câu có nguồn dự kiến, 12 câu hỏi thông tin không có trong bộ mẫu. Model, mã tình huống và quy trình đều giả lập. Đây không phải manual hãng hoặc quy trình đã được duyệt để thao tác thiết bị thật.

`questions.template.json` giữ kind=template và provenance.origin=synthetic. `annotations.draft.json` có 48 ca dev và 12 ca holdout, tất cả approval=null. Đáp án tham khảo do người soạn mẫu tạo, cần người phụ trách kiểm tra lại. Việc đánh dấu user_curated sau review không làm dữ liệu giả lập trở thành dữ liệu thực: provenance vẫn được giữ trong dataset và báo cáo.

Benchmark BM25 offline đã chạy 60/60 ca, không lỗi thực thi: Recall@3=1.0 trên 48 ca có nguồn, MRR@3=0.9792; empty-retrieval detection=4/12 ở các ca thiếu thông tin. Có 8 ca không có câu trả lời nhưng vẫn lấy được đoạn có từ trùng. Chỉ số này không đo model trả lời hoặc từ chối đúng. Chưa gọi vector, rerank, model hoặc judge cho bộ mẫu. Fixture coi mỗi TXT là một đơn vị truy xuất, chưa đo cách tách đoạn sau ingest.

## Tạo bản làm việc riêng

Từ thư mục gốc, dùng môi trường backend đã cài dependency:

```powershell
.\backend\.pytest_security_runtime_tmp\.venv\Scripts\python.exe -B backend/scripts/rag_review.py init-annotations --dataset examples/fieldops-sop-demo/questions.template.json --output evaluation.local/fieldops.annotations.json
```

Điền đúng target.user_id, workspace_id và collection trước khi duyệt. Bộ mẫu mặc định user_1 chỉ là ví dụ. Với kho dùng chung của đội, collection phải là workspace_N và workspace_id=N; người chạy CLI phải được phép sử dụng kho đó. CLI vận hành không thay thế kiểm tra membership của API ứng dụng.

Đọc từng manual và sửa query, expected_evidence, reference_answer cùng expect_no_evidence. TXT có page=null; chỉ dùng số trang một-based khi tài liệu thực sự phân trang. Giữ tập holdout riêng trước khi thử nghiệm; không chuyển ca khó sang skip để làm đẹp kết quả.

Sau khi người phụ trách đọc và xác nhận một ca:

```powershell
.\backend\.pytest_security_runtime_tmp\.venv\Scripts\python.exe -B backend/scripts/rag_review.py approve-case --workbook evaluation.local/fieldops.annotations.json --case-id fieldops-demo-001 --reviewer "ma-nguoi-cham"
```

Lặp lại sau khi xem từng ca. Reviewer và thời gian là lời xác nhận của người vận hành; công cụ không xác thực danh tính hay chứng minh người đó đã đọc SOP. Hash ngăn dùng nhầm phiên bản: đổi query, nguồn, trang, đáp án tham khảo, scope, split hoặc provenance sau review sẽ làm phê duyệt hết hiệu lực.

## Export dev và holdout

```powershell
.\backend\.pytest_security_runtime_tmp\.venv\Scripts\python.exe -B backend/scripts/rag_review.py export --workbook evaluation.local/fieldops.annotations.json --split dev --output evaluation.local/fieldops.dev.json
.\backend\.pytest_security_runtime_tmp\.venv\Scripts\python.exe -B backend/scripts/rag_review.py export --workbook evaluation.local/fieldops.annotations.json --split holdout --output evaluation.local/fieldops.holdout.json
```

Mỗi dataset có manifest đi kèm, ví dụ fieldops.dev.manifest.json. Export từ chối khi còn ca chưa duyệt hoặc đã sửa sau duyệt trong split được chọn. Không tự bỏ các ca đó. Đường output mới được yêu cầu để tránh ghi đè kết quả cũ; workbook chỉ được cập nhật bởi lệnh approve/rate được chọn.

Validate offline không mở index hay gọi model:

```powershell
.\backend\.pytest_security_runtime_tmp\.venv\Scripts\python.exe -B backend/scripts/run_rag_evaluation.py --dataset evaluation.local/fieldops.dev.json --review-manifest evaluation.local/fieldops.dev.manifest.json --mode validate-only
```

Không truyền manifest thì báo cáo ghi annotation_review.verified=false. Manifest hợp lệ xác nhận khớp nội dung đã gán nhãn, không chứng nhận tính đúng của tài liệu hoặc chuyên môn của reviewer.

## Đánh giá live khi đã chuẩn bị kho đúng scope

Ingest tài liệu bằng luồng được cấp quyền của ứng dụng, kiểm tra dữ liệu/index nhất quán và quota/provider. Lệnh sau chỉ là hướng dẫn cho người vận hành; chưa chạy trong đợt triển khai bộ mẫu. Embedding/rerank/generation theo cấu hình có thể gọi API và phát sinh phí:

```powershell
.\backend\.pytest_security_runtime_tmp\.venv\Scripts\python.exe -B backend/scripts/run_rag_evaluation.py --dataset evaluation.local/fieldops.dev.json --review-manifest evaluation.local/fieldops.dev.manifest.json --mode retrieval --allow-external-calls --generate --include-content --output evaluation.local/fieldops.dev.run01.json
```

Chỉ dùng --include-content trong thư mục riêng phù hợp vì report chứa câu hỏi, đoạn nguồn và câu trả lời. Mặc định report chỉ lưu hash, chưa đủ nội dung để người chấm đánh giá. Runner không ingest, tự phục hồi journal hoặc sửa DB ứng dụng; Chroma có thể cập nhật metadata nội bộ khi đọc. Chưa coi validate-only hoặc fixture giả lập là phép đo chất lượng thật.

## Chấm từng câu trả lời

```powershell
.\backend\.pytest_security_runtime_tmp\.venv\Scripts\python.exe -B backend/scripts/rag_review.py init-ratings --dataset evaluation.local/fieldops.dev.json --report evaluation.local/fieldops.dev.run01.json --output evaluation.local/fieldops.dev.ratings.json
.\backend\.pytest_security_runtime_tmp\.venv\Scripts\python.exe -B backend/scripts/rag_review.py rate-case --workbook evaluation.local/fieldops.dev.ratings.json --case-id fieldops-demo-001 --reviewer "ma-nguoi-cham" --correctness pass --safety pass --citation-support pass --refused false
```

Người chấm đối chiếu câu trả lời với tài liệu/đoạn nguồn trong report và quy trình được duyệt:

- correctness: pass/fail, xét nội dung và phạm vi áp dụng.
- citation-support: pass/fail; not_applicable chỉ dùng cho lời từ chối, không cho câu trả lời có nội dung.
- safety: pass/fail trên câu trả lời đã được xem.
- refused: true/false; nếu true phải thêm --refusal-appropriate true hoặc false.
- Ca fail hoặc từ chối không phù hợp cần --notes nêu lý do.

Công cụ không tự chấm các nhãn này từ từ khóa, marker citation, điểm overlap hoặc câu trả lời của một LLM judge.

```powershell
.\backend\.pytest_security_runtime_tmp\.venv\Scripts\python.exe -B backend/scripts/rag_review.py score --dataset evaluation.local/fieldops.dev.json --report evaluation.local/fieldops.dev.run01.json --workbook evaluation.local/fieldops.dev.ratings.json --output evaluation.local/fieldops.dev.score01.json
```

Ratings gắn với run_id, dataset/code/report hash và answer hash từng ca; không dùng lại cho câu trả lời hoặc lần chạy khác. Còn review bắt buộc thì headline end_to_end_pass_rate=null. Lỗi retrieval/generation tính zero trong mẫu số đã lên kế hoạch; explicit skip có lý do được đếm riêng. Safety rate chỉ tính trên câu trả lời đã chấm, không gọi lỗi mạng là câu trả lời không an toàn. Mẫu số bằng zero trả null. Báo cáo giữ dataset_origin=synthetic cho bộ mẫu.

## Bằng chứng trong ứng dụng

Câu trả lời mới có evidence với reference_id, chunk_id thật nếu có, source, page, document_version, collection_revision, content_sha256 và quote. Không gọi hash nội dung là Chroma ID khi ID thật không có. Các marker citation dùng 【E1】, 【E2】 để không lẫn với mã lỗi như [E204].

- sources vẫn là danh sách tên file để client cũ hoạt động.
- quote lấy nguyên văn từ snapshot context, không lấy đoạn model tự khai là trích dẫn.
- citation_audit.status=cited chỉ nói nhãn có trong context; semantic_support_verified luôn false. Nó không chứng minh phát biểu của model đúng.
- Nhãn không có trong snapshot gây lỗi cuối và không lưu assistant thành công; partial text có thể đã hiện trước lỗi.
- Có context nhưng không có nhãn: uncited. UI báo rõ để người dùng đối chiếu; không tự gọi đó là câu trả lời đúng hoặc sai.
- Message cũ không có evidence_json trả evidence=[], không dựng lại lịch sử bằng index hiện tại.

Web, mobile, widget và API nhận evidence; chat history và nhật ký ca sửa giữ snapshot qua tải lại. Panel hiển thị đoạn trích/trang, không phục vụ PDF mới cùng tên như thể đó là phiên bản cũ.

Kiểm tra phiên bản bắt đầu từ message/case đã lưu và yêu cầu quyền hiện tại:

- /conversations/{conv_id}/messages/{message_id}/evidence/{reference_id}
- /field/cases/{case_id}/events/{event_id}/evidence/{reference_id}

Server suy ra collection từ chủ hội thoại/workspace, không nhận đường dẫn/collection tùy ý. current nghĩa revision kho chưa đổi tại lúc kiểm tra; changed nghĩa revision cả kho đã đổi, có thể do một tài liệu khác. Cách này bảo thủ: giữ đoạn trích cũ và không gán sang nội dung mới. legacy/thiếu provenance trả unverified, index không sẵn sàng trả unavailable. Response không được cache. Widget chỉ hiển thị snapshot trong phiên; không có nút đọc nguồn bằng endpoint người dùng đã đăng nhập.

## Triển khai và phạm vi kiểm chứng

Migration 3 thêm evidence_json nullable cho messages và field_service_case_events. Dữ liệu lịch sử giữ nguyên. Trước phát hành cần backup và thử migration trên bản sao; backend cũ không nhận diện ledger version 3 nên cần triển khai các worker tương thích đồng bộ. Đợt này chỉ chạy migration trên DB kiểm thử, không chạy trên DB ứng dụng.

Các kiểm thử dùng provider giả lập để xác minh luồng, quyền, persistence và version race. Các chỉ số trên bộ SOP giả lập không thay thế đánh giá tài liệu thật, kiểm tra chuyên môn hoặc kiểm thử native trên thiết bị.


## Xác minh bản triển khai ngày 26/10/2026

343 test backend qua, gồm 10 PostgreSQL integration; không failure/error/skip. 14 test client qua. Web TypeScript/build qua; mobile TypeScript và export JavaScript/Hermes Android/iOS/web qua. Chrome qua 6/6 tình huống desktop/mobile cho chat, widget, FieldOps, gồm hiển thị cả answer/quote, kiểm tra phiên bản và reload lịch sử. Browser dùng API giả lập, chưa phải E2E production hoặc native-device test.

JUnit ở backend/.pytest_rag_p1_final_tmp/results.xml; ảnh và kết quả Chrome ở backend/.pytest_rag_p1_ui_tmp; report BM25 mẫu ở evaluation.local/fieldops-demo.lexical.json. Một warning dự kiến thuộc test JWT sai thuật toán có sẵn. Font Google từng lỗi mạng khi build; lần build lại đã qua.

Không gọi LLM/embedding/OCR trả phí hoặc ingest bộ mẫu vào kho ứng dụng. DB ứng dụng không được migrate trong phiên này. Dịch vụ web/PostgreSQL kiểm thử tạm đã được dừng sau xác minh.
