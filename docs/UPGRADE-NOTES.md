# Ghi chú nâng cấp bảo mật và dữ liệu

## Thay đổi hành vi

- Access JWT có purpose, user ID và token_version. Token phát hành trước bản nâng cấp không còn hợp lệ; người dùng cần đăng nhập lại một lần.
- Link reset mới dùng một lần. Đổi mật khẩu tăng token_version và thu hồi các access token trước đó.
- Xóa tài khoản chuyển thành vô hiệu hóa danh tính, thu hồi membership/API keys/embed sessions và giữ ID phục vụ audit ca sửa. Không tái sử dụng ID. Không vô hiệu hóa trưởng đội cuối cùng khi đội còn thành viên khác.
- Widget cần cả conversation_id và session_token riêng để tiếp tục phiên khách. Client widget trong repo đã gửi trường mới; client tích hợp riêng cần cập nhật tương tự.
- Web lưu thao tác trước khi gửi, đợi IndexedDB commit, tách hàng đợi theo API/tài khoản/workspace. Lỗi 401/408/429/5xx giữ để gửi lại; lỗi nghiệp vụ được giữ trong danh sách cần xử lý.
- Hàng đợi web cũ thiếu thông tin chủ sở hữu được giữ lại nhưng không tự gửi dưới tài khoản hiện tại.
- Mobile dùng SecureStore cho token native, giữ phần JSON streaming chưa đủ dòng và cập nhật trạng thái đăng nhập khi gặp 401.
- Upload chuẩn bị file và embeddings trước khi công bố. Các lỗi xử lý thông thường khôi phục file/index cũ; đọc và ghi index dùng khóa chung giữa API/batch process, cache dùng revision.
- Webhook Stripe bắt buộc có secret chữ ký và price Pro, xác minh raw payload, đối chiếu subscription hiện tại theo customer đã liên kết. Không cấp quyền bằng metadata user_id.
- Endpoint /de/gold/stats yêu cầu admin.

## Trước khi triển khai

1. Dừng các writer và sao lưu nhất quán app.db, uploads, chroma_db cùng thư mục .rag-locks trong DATA_DIR. Kiểm tra khả năng phục hồi bản sao ở môi trường riêng.
2. Chạy các kiểm thử bên dưới trong checkout này. Không chạy kiểm thử trên dữ liệu production.
3. Thử startup với bản sao dữ liệu: SQLite tự bổ sung token_version/deleted_at/billing_sync_version và bảng credential/session. Migration giữ lại danh tính mồ côi từ dữ liệu cũ dưới dạng tài khoản vô hiệu hóa.
4. Đặt APP_ENV=production và SECRET_KEY riêng dài ít nhất 32 ký tự. Khóa mặc định/placeholder sẽ làm startup thất bại. Cấu hình đủ STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_PRICE_PRO nếu dùng billing. Đặt CORS_ORIGINS bằng origin chính xác của web; production/staging không tự cho phép localhost/LAN.
5. Triển khai đồng thời backend và client widget mới; đăng nhập lại và kiểm tra offline/reconnect trên thiết bị thật trước khi mở rộng pilot.

## Kiểm tra cục bộ

Python 3.11–3.12 và Node phù hợp lockfile; mobile Expo 57 cần Node 22.13+.

```powershell
.\backend\.pytest_security_runtime_tmp\.venv\Scripts\python.exe -B -m pytest tests/ -q -p no:cacheprovider
node --test tests/client_state.test.cjs
```

Trong frontend và mobile, chạy:

```powershell
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

Frontend cần chạy thêm npm run build khi xác minh bản phát hành. Các kiểm thử mới giả lập dịch vụ model/Stripe, không gọi API trả phí.

## Phạm vi còn lại

- Publish có journal phục hồi khi process bị gián đoạn; nhiều máy chỉ phối hợp được nếu dùng cùng filesystem hỗ trợ khóa. Giới hạn mất điện/hỏng đĩa được nêu ở phần Đợt 2.
- Warehouse lineage/workspace và benchmark RAG có sinh câu trả lời trên dữ liệu thực còn cần triển khai riêng. Quota độc lập và benchmark BM25 tổng hợp đã có ở Đợt 2.
- Dependency đã được kiểm chứng trong venv sạch ở Đợt 3; venv cũ có package thừa/lệch phiên bản và cần được thay khi triển khai. Audit dependency và các advisory chưa có bản sửa được ghi ở Đợt 4; chưa triển khai cloud.
- Migration ứng dụng có phiên bản cho SQLite/PostgreSQL đã có ở Đợt 3. Chỉ các schema legacy đã biết được tự nâng cấp; schema thiếu cột khác sẽ dừng startup để yêu cầu migration riêng.

- Medallion vẫn được cập nhật trước Chroma và chưa chung transaction với raw/index; chưa coi warehouse và index là một commit nguyên tử.
- Mobile offline v1 cũ chưa lưu API origin: lần đầu nâng cấp gắn namespace vào địa chỉ API hiện có. Lịch sử đổi server trước bản nâng cấp không thể suy ra tự động.
- Trưởng đội cuối cùng còn thành viên bị chặn vô hiệu hóa (409); endpoint chuyển quyền trưởng đội chưa thuộc bản sửa này.

## Đợt 2: quota, phục hồi và đánh giá truy xuất

### Hạn mức dùng chung

- Bảng daily_query_usage lưu số yêu cầu xử lý RAG theo user và ngày UTC. Chat, API key, widget và trợ lý ca sửa dùng cùng hạn mức.
- Mỗi yêu cầu được giữ lượt bằng cập nhật DB nguyên tử trước khi vào RAG. Lỗi chuẩn bị nội bộ trước RAG được hoàn lượt; lỗi provider, kết nối bị ngắt hoặc lỗi lưu kết quả sau đó vẫn tính là yêu cầu đã được nhận xử lý.
- Ngày hạn mức đổi lúc 00:00 UTC (07:00 tại Việt Nam/Thái Lan). Xóa hội thoại không làm giảm số đã dùng.
- Startup sao chép usage còn lưu của ngày nâng cấp đúng một lần, gồm assistant messages và các sự kiện assistant_recommendation của ca sửa. Không thể khôi phục lượt đã bị xóa khỏi lịch sử trước lần nâng cấp.
- Thống kê quản trị và analytics dùng counter cho ngày đã có bản ghi; ngày cũ chưa có counter giữ cách thống kê từ câu trả lời lưu trong lịch sử.
- SQLite và PostgreSQL 16 đều đã có kiểm thử cạnh tranh quota bằng nhiều connection thực; bộ kiểm thử PostgreSQL dùng schema tạm, không dùng DB ứng dụng.

### Phục hồi lần ghi bị gián đoạn

- Trước khi đổi raw/index, worker lưu journal, snapshot IDs/nội dung/metadata/embeddings và bản sao raw có checksum. Journal hợp lệ xác nhận lần ghi chưa committed thì phục hồi phiên bản trước; lần đã committed chỉ dọn phần tạm, kể cả khi bước fsync/cleanup sau đó báo lỗi.
- Nếu không xác định được commit marker, hệ thống giữ backup và chặn đọc/ghi để điều tra; không tự suy đoán hoàn tác. Marker commit_uncertain cũng chặn phục hồi tự động.
- Startup phục hồi journal trước khi phục vụ API. Reader cũng kiểm tra journal trước cache/truy xuất; journal hoặc backup hỏng làm thao tác dừng thay vì trả index đang trộn phiên bản.
- Không xóa hoặc sửa thủ công journal/backup để ép ứng dụng chạy. Khi không phục hồi được, giữ toàn bộ dữ liệu và dùng bản sao nhất quán để điều tra/khôi phục.
- Đã kiểm thử process bị terminate giữa các bước trên Windows, với dữ liệu index được lưu thực. Điều này không bảo đảm dữ liệu khi đĩa hỏng hoặc máy mất điện; độ bền vẫn phụ thuộc filesystem và cơ chế ghi của Chroma. Windows không có directory fsync qua API Python portable.
- Warehouse Medallion vẫn chưa nằm trong cùng transaction với raw/index.
- Sau khi process bị dừng, thư mục upload tạm `.document-stage-*` có thể còn lại dù raw/index đã được phục hồi; tự động dọn các thư mục mồ côi này chưa thuộc đợt hiện tại.

### Benchmark FieldOps offline

```powershell
.\backend\.pytest_security_runtime_tmp\.venv\Scripts\python.exe -B backend/scripts/run_eval_benchmark.py --offline --output fieldops_eval_results.json --fail-on-regression
```

Fixture backend/evaluation/fieldops-v1.json có 16 tình huống tổng hợp, gồm 13 ca có bằng chứng và 3 ca không có bằng chứng. Báo cáo ghi dataset/code fingerprint, phiên bản runtime, planned/completed/failed và Recall@k/MRR.

Đây là kiểm thử BM25 trên fixture; chưa đo vector, rerank, câu trả lời LLM hay độ an toàn của hướng dẫn thực tế. Không coi empty retrieval là bằng chứng mô hình sẽ từ chối trả lời. Chỉ số grounding hiện là chẩn đoán trùng từ kèm phát hiện phủ định cơ bản, không phải độ chính xác ngữ nghĩa.


## Đợt 3: dependency, migration PostgreSQL và đánh giá RAG thực

### Môi trường tái lập

- Requirements khai báo trực tiếp langchain-core thay cho package langchain không được dùng. Provider langchain-openai tồn dư trong venv cũ không thuộc dependency của ứng dụng.
- Công cụ `backend/scripts/check_dependencies.py` kiểm tra phiên bản của 27 package khai báo, `pip check` và 18 nhóm import backend dùng. Chạy bằng chính interpreter sẽ khởi động ứng dụng; không load cấu hình ứng dụng hoặc gọi model.
- Đã cài và kiểm chứng một venv riêng từ đầu trên Windows/Python 3.12.4 tại `backend/.pytest_dependency_tmp/.venv`. Venv `.venv` cũ vẫn có package lệch phiên bản; kết quả kiểm thử Đợt 3 dùng venv mới. Khi phát hành, tạo venv mới từ requirements rồi kiểm tra, không chỉ cài chồng vào venv cũ.
- CI được cấu hình kiểm tra Python 3.11 và 3.12; Docker dùng 3.12. Ràng buộc NumPy hiện tại chưa hỗ trợ Python 3.13+. Kiểm chứng tương thích không thay thế audit lỗ hổng dependency.

```powershell
.\backend\.pytest_security_runtime_tmp\.venv\Scripts\python.exe -B backend/scripts/check_dependencies.py
```

### Migration ứng dụng

- Startup chạy migration trước usage backfill và trước phục vụ request. Bảng `schema_migrations` ghi version, tên và thời điểm; version/tên không nhận diện được làm startup thất bại.
- SQLite dùng BEGIN IMMEDIATE; PostgreSQL dùng transaction advisory lock, bao gồm kiểm tra schema, DDL, sửa dữ liệu legacy và tạo bảng còn thiếu. Migration lặp lại không áp dụng lại version cũ.
- Các cột legacy đã biết được bổ sung; paid plan/token_version/billing_sync_version hiện hữu được giữ. Lịch sử tham chiếu user đã hard-delete được giữ bằng danh tính vô hiệu hóa; credential tương ứng bị thu hồi.
- PostgreSQL dùng boolean FALSE đúng dialect và bảo đảm sequence user ID không lùi sau khi giữ lại các ID lịch sử. Sequence của PostgreSQL không rollback nên migration lỗi có thể để lại khoảng trống ID.
- Sau migration, kiểm tra tất cả cột mapped. Schema lạ thiếu cột sẽ rollback và dừng startup; không dùng create_all để che thiếu cột. Chưa kiểm tra mọi khác biệt kiểu dữ liệu/index/constraint của schema tùy biến.
- Đã kiểm thử trên PostgreSQL 16 tạm với schema UUID: fresh/legacy, startup đồng thời, rollback khi lỗi, giá trị mặc định và bảo toàn sequence. Quota cũng được kiểm tra cạnh tranh, backfill đúng một lần và giữ số dùng sau xóa hội thoại/đổi ngày UTC.
- Trước triển khai vẫn cần dừng writer, sao lưu nhất quán và thử startup trên bản sao. DB user chạy migration cần quyền DDL trong schema/sequence. Không có tự động downgrade phá hủy dữ liệu.

CI cấu hình PostgreSQL service riêng. Khi chạy integration test cục bộ, biến `FIELDOPS_TEST_POSTGRES_URL` phải trỏ DB kiểm thử riêng; mỗi test tạo và xóa schema UUID. Không đặt biến này thành DB production.

### Bộ đánh giá trên tài liệu được xác nhận

- `backend/evaluation/rag-real.schema.json` mô tả dữ liệu; `rag-real.template.json` chỉ là mẫu, không phải bộ tài liệu thực đã đánh giá. Tạo bản riêng trong `evaluation.local/`, điền đúng user/workspace/collection và đánh dấu kind=user_curated sau khi người phụ trách xác nhận query, nguồn và số trang.
- Mặc định chỉ validate offline, không import cấu hình/provider hoặc truy xuất index:

```powershell
.\backend\.pytest_security_runtime_tmp\.venv\Scripts\python.exe -B backend/scripts/run_rag_evaluation.py --mode validate-only
```

- Khi đã có dữ liệu được xác nhận, dùng `--dataset evaluation.local/pilot.json --mode retrieval --allow-external-calls --output rag_eval_results.pilot.json`. Embedding và rerank theo cấu hình có thể phát sinh phí; chưa chạy các lời gọi này trong đợt nâng cấp.
- Runner đọc collection tồn tại, bỏ qua Redis cache, yêu cầu cả nhánh vector/BM25 thành công và giữ chính sách rerank của ứng dụng. Đây là chính sách đánh giá chặt hơn fallback của API. Pending/corrupt journal làm case thất bại; công cụ không tự phục hồi hoặc ingest tài liệu.
- Report ghi fingerprint dataset/mã, runtime/config không chứa secret, revision collection từng case, planned/completed/failed/skipped, Recall@k và MRR. Case truy xuất lỗi tính zero, không tính là thành công không tìm thấy bằng chứng.
- Sinh câu trả lời cần thêm `--generate`; kết quả yêu cầu người đánh giá xác nhận correctness/safety/refusal. Không suy ra độ chính xác từ trùng từ hoặc empty retrieval. Mặc định report chỉ lưu hash; dùng `--include-content` nếu cần nội dung query/evidence/answer để review và lưu report tại nơi phù hợp.
- Chưa có kết quả chất lượng RAG trên dữ liệu thực được gán nhãn; các kiểm thử của runner dùng pipeline giả lập và xác minh schema/chính sách báo cáo.


### Xác minh cục bộ ngày 22/10/2026

- Windows/Python 3.12.4 trong venv sạch: 243 test backend qua, không failure/error/skip; bao gồm 10 test tích hợp PostgreSQL 16. Báo cáo JUnit ở `backend/.pytest_stage3_verified_tmp/results.xml` (artifact cục bộ, không commit).
- Có 28 cảnh báo deprecated từ Starlette/AnyIO và python-jose; chưa thay các package này trong đợt kiểm chứng tương thích.
- Dependency check: 27 package khai báo đúng ràng buộc, pip check sạch, 18 nhóm import qua.
- Benchmark BM25 tổng hợp: 16/16 hoàn tất, không lỗi; Recall@3/MRR@3/no-evidence detection đều 1.0 trong phạm vi fixture.
- Runner dữ liệu thực: validate-only hợp lệ, 2 case được xác minh schema và bỏ qua truy xuất; không có model/API được gọi. Chưa có phép đo chất lượng trên dữ liệu thực.
- Docker Compose config hợp lệ; cấu hình GitHub Actions Linux/Python 3.11/3.12 chưa được chạy từ phiên làm việc này.


## Đợt 4: bản vá dependency, JWT và CORS

Đã nâng các gói backend có bản sửa, chuyển python-jose sang PyJWT, nâng web lên Next.js 16/React 19/Tailwind 4 và cập nhật các bản vá tương thích Expo 57. Production CORS yêu cầu origin tường minh. JWT đã có purpose/version của Đợt 1 giữ tương thích qua migration thư viện.

Windows/Python 3.12.4 trong venv mới: 272 test backend qua, gồm 10 PostgreSQL test; 14 client regression và 12 tình huống Chrome qua. Web build/typecheck và audit đầy đủ/production qua; mobile Expo check, TypeScript và export Android/iOS/web qua. Các kiểm tra JavaScript/bundle chưa thay thế kiểm tra native trên thiết bị thật.

Backend còn 4 advisory Chroma chưa có bản sửa. Mobile lần audit thành công sau cập nhật còn 15 package high, quy về 2 advisory gốc braces/node-forge; không còn critical. Không ignore advisory hoặc hạ Expo bằng audit fix --force. Chi tiết phiên bản, bằng chứng, giới hạn audit và việc cần làm trước phát hành ở [DEPENDENCY-SECURITY.md](DEPENDENCY-SECURITY.md).

Các lệnh kiểm tra trong tài liệu hiện trỏ venv Đợt 4 tại backend/.pytest_security_runtime_tmp/.venv; số liệu ở phần Đợt 3 là kết quả lịch sử. Cần môi trường mới khi triển khai để không giữ python-jose/ecdsa tồn dư.

Lockfile mobile cuối đã được đối chiếu trực tiếp với API advisory chính thức của npm (457 tên package): còn 2 advisory gốc. Các lần CLI audit sau khi khóa expo-font bị timeout; số 15 package ở trên là lần CLI thành công ngay trước bước khóa font. Chi tiết phương pháp và hash bằng chứng ở docs/audit-summary.json.


## Đợt 5: lỗi lõi RAG và lộ trình chất lượng — 26/10/2026

Đã sửa chain từng biến lỗi provider thành token rồi báo done; giới hạn buffer/worker stream, truyền hủy và đóng iterator; xác minh kết thúc stream Ollama/Gemini; không lưu assistant thành công khi provider hoặc DB lỗi. Lỗi công khai dùng thông báo cố định. API/field trả 503 khi generation rỗng/lỗi. Quota vẫn tính yêu cầu đã nhận theo chính sách hiện có.

Cache dùng hash cấu trúc và namespace v2 để tách source/query không nhập nhằng; Redis có timeout ngắn và bỏ qua khi lỗi. Chat/widget/API kiểm tra cấu hình sinh văn bản theo provider, cho phép chọn Ollama; embedding/OCR Gemini vẫn cần cấu hình riêng. LLM_REQUEST_TIMEOUT_SEC mặc định 60 giây là timeout I/O, không phải deadline tổng; các giới hạn hủy thread được ghi rõ trong lộ trình.

304 test backend qua, gồm 10 PostgreSQL test và 32 ca regression thêm; 14 test client qua. Test dùng provider giả lập và DB tạm. Chưa có phép đo chất lượng câu trả lời trên tài liệu thật. [RAG-ROADMAP.md](RAG-ROADMAP.md) nêu lỗi đã sửa, rủi ro chất lượng còn lại và các ưu tiên có tiêu chí nghiệm thu.


## Đợt 6: evidence, đánh giá có người chấm và SOP mẫu — 26/10/2026

Migration 3 thêm evidence_json nullable cho messages/field_service_case_events. Nguồn cũ giữ nguyên; không tái tạo evidence cho lịch sử legacy. Các client/API giữ sources và thêm evidence/citation_audit. Endpoint mở snapshot kiểm tra chủ hội thoại hoặc membership hiện tại; so revision kho và giữ quote lịch sử khi đã đổi. Chưa có PDF viewer cho phiên bản raw cũ; UI mở đoạn trích đã lưu cùng số trang.

Công cụ rag_review.py tạo annotation, ghi nhận review theo case, export dev/holdout+manifest, tạo ratings đúng run/answer và tính quality với coverage rõ ràng. Provenance synthetic vẫn được giữ sau khi người dùng duyệt bộ mẫu. Chưa có nhãn chuyên môn thật được tự tạo hoặc tự xác nhận.

343 test backend gồm 10 PostgreSQL và 14 test client qua; web build/typecheck, mobile typecheck/export và 6 tình huống Chrome qua. Bộ 8 TXT/60 câu giả lập được kiểm tra offline, không gọi provider. [Hướng dẫn](RAG-EVALUATION-GUIDE.md) và [bộ mẫu](../examples/fieldops-sop-demo/README.md) có bước dùng và giới hạn. Cần thử migration trên bản sao và triển khai worker tương thích trước phát hành.

## Technical RAG opt-in — 26/10/2026

Đã có mã riêng tại `backend/rag/technical/`: layout HTML bảng, section parent–child, SQLite BM25 + Chroma child vectors chạy song song, RRF, real cross-encoder local/Cohere v2, và LangChain chọn ID để server trả nguyên văn parent. Scope thiết bị/revision bắt buộc; approval hash bao extraction và metadata; index publication theo generation.

64 test mô-đun qua với profile LangChain 1.7.0/Pydantic 2.14.1; 159 regression RAG qua trên môi trường ứng dụng. Providers inference giả lập, SQLite/Chroma thật; chưa chạy OCR/LLM/BGE thật hay đánh giá chất lượng trên manual nhà sản xuất. Chưa thay endpoint chat, reindex uploads hoặc triển khai. Xem [hướng dẫn đầy đủ](TECHNICAL-RAG.md).

## Final Production Audit — 28/10/2026

Nginx HTTPS, Compose isolation/file secrets, Redis/Kafka lifecycle, Telecom fail-open và Celery recycle/cgroup đã có source/test. Xem [báo cáo phạm vi và giới hạn](FINAL-PRODUCTION-AUDIT.md); không suy ra zero-CVE, HA hoặc độ chính xác model từ kết quả vận hành.
