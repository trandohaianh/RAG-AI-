# Các quyết định kỹ thuật của FieldOps

Mỗi quyết định ghi 5 ý: vấn đề, các phương án đã cân nhắc, lý do chọn, ưu điểm và hạn chế,
và nếu làm tiếp sẽ đổi gì. Con số trong file đều lấy từ mã nguồn, cấu hình hoặc báo cáo trong
repo; chỗ nào là phương án chỉ cân nhắc khi thiết kế (không có thử nghiệm trong repo) thì ghi rõ.

Mục lục:

1. [RAG thay cho huấn luyện lại](#1-rag-thay-cho-huấn-luyện-lại)
2. [Tìm kiếm lai BM25 + vector và xếp hạng lại](#2-tìm-kiếm-lai-bm25--vector-và-xếp-hạng-lại)
3. [Mã riêng cho mỗi thao tác](#3-mã-riêng-cho-mỗi-thao-tác)
4. [Kho tài liệu theo đội](#4-kho-tài-liệu-theo-đội)
5. [Quyền sửa tài liệu](#5-quyền-sửa-tài-liệu)
6. [Độ bám nguồn theo từ vựng](#6-độ-bám-nguồn-theo-từ-vựng)
7. [Chấm điểm theo mẫu](#7-chấm-điểm-theo-mẫu)
8. [Webhook CRM/NMS ký HMAC, tự gửi lại](#8-webhook-crmnms-ký-hmac-tự-gửi-lại)
9. [Chạy gọn trên một máy miễn phí](#9-chạy-gọn-trên-một-máy-miễn-phí)

---

## 1. RAG thay cho huấn luyện lại

**Vấn đề.** Kỹ thuật viên cần câu trả lời theo cẩm nang riêng của đội. Cẩm nang thay đổi liên
tục (model thiết bị mới, quy trình sửa, cảnh báo an toàn), và người dùng phải kiểm chứng được
câu trả lời lấy từ đâu. Chatbot thông thường không biết cẩm nang nội bộ và vẫn trả lời khi
không biết.

**Các phương án đã cân nhắc.**
- Dùng mô hình ngôn ngữ có sẵn, không nạp tài liệu: không biết cẩm nang của đội.
- Huấn luyện lại (fine-tune) mô hình trên cẩm nang: phương án cân nhắc khi thiết kế, không
  có thử nghiệm trong repo.
- RAG: giữ nguyên mô hình, mỗi câu hỏi đi tìm đoạn tài liệu liên quan rồi mới viết câu trả lời.

**Lý do chọn.** Chọn RAG. Tài liệu vừa nạp xong là tra được ngay, không phải chờ và trả tiền
huấn luyện lại sau mỗi lần sửa cẩm nang. Mỗi câu trả lời kèm tên tài liệu nguồn. Không tìm
thấy căn cứ thì hệ thống không gọi AI mà nói thẳng là chưa có tài liệu.

**Ưu điểm và hạn chế.**
- Ưu: cập nhật tức thì, có nguồn để kiểm chứng, đổi được mô hình (Gemini, Ollama) mà không
  làm lại dữ liệu.
- Hạn chế: chất lượng phụ thuộc bước truy xuất; tìm sai đoạn thì trả lời sai dù vẫn có trích
  nguồn. Embedding và OCR vẫn dùng Gemini, nên chế độ sinh văn bản local (Ollama) chưa có nghĩa
  là toàn bộ pipeline chạy offline (`docs/RAG-ROADMAP.md`).

**Nếu làm tiếp sẽ đổi gì.** Bắt đầu bằng bộ đánh giá trên tài liệu thật do người phụ trách
gán nhãn (50–100 câu, mục P1 trong `docs/RAG-ROADMAP.md`). Hiện bộ eval 68 câu là dữ liệu
mẫu giả lập.

## 2. Tìm kiếm lai BM25 + vector và xếp hạng lại

**Vấn đề.** Câu hỏi hiện trường chứa nhiều mã chính xác như `E-204`, `GPON-2412B`. Tìm theo
nghĩa (vector) dễ trả về đoạn "gần giống" nhưng sai mã; tìm theo từ khoá thì bỏ lỡ câu hỏi
diễn đạt khác tài liệu.

**Các phương án đã cân nhắc.**
- Chỉ vector (ChromaDB).
- Chỉ từ khoá BM25.
- Tìm kiếm của PostgreSQL: `ts_rank` (không phải BM25, tìm kém hơn) hoặc ParadeDB `pg_search`
  (chỉ có nghĩa khi đã chuyển sang PostgreSQL và phải viết lại bộ tách từ) — ghi trong README,
  mục "Giới hạn kiến trúc đã chọn" (F3-A).
- Gộp kết quả bằng cộng điểm thô, hoặc bằng RRF (gộp theo thứ hạng).

**Lý do chọn.** Chạy song song BM25 tự viết (`backend/rag/bm25.py`, BM25L, bộ tách từ giữ
nguyên mã lỗi, mã model và dấu tiếng Việt) và vector, gộp bằng RRF với hằng số 60. RRF chỉ dùng
thứ hạng, vì điểm cosine và điểm BM25 không cùng thang đo nên không cộng trực tiếp được. Lấy 15
đoạn (`RETRIEVE_CANDIDATES`), xếp hạng lại còn 5 (`TOP_K`); dùng Cohere Rerank khi có
`COHERE_API_KEY`. Lần đổi cách xếp hạng lại để giữ thứ tự của bước tìm kiếm lai được đo trước
và sau: MRR tăng từ 0,9121 lên 0,9636, tỉ lệ đạt giữ nguyên 0,7206 trên 68 câu
(`reports/04_Danh_gia_da_vai_tro_va_thuat_toan.md`).

**Ưu điểm và hạn chế.**
- Ưu: bắt đúng mã thiết bị và vẫn hiểu câu hỏi diễn đạt khác; không cần dịch vụ tìm kiếm riêng.
- Hạn chế: chỉ mục BM25 nằm trong bộ nhớ của từng tiến trình, nên chỉ chạy được một tiến trình
  API (xem quyết định 9). Kho mẫu chỉ có 8 tài liệu với k = 5, nên recall@5 gần như luôn bằng 1;
  phải nhìn MRR và nDCG (`backend/evals/README.md`).

**Nếu làm tiếp sẽ đổi gì.** Thử chia đoạn theo mục và bảng của tài liệu thật, rồi hiệu chỉnh
lại tìm kiếm lai và ngưỡng thiếu bằng chứng (mục P2 trong `docs/RAG-ROADMAP.md`). Khi chuyển
sang PostgreSQL thì cân nhắc lại `pg_search`.

## 3. Mã riêng cho mỗi thao tác

**Vấn đề.** Mạng ở hiện trường hay đứt giữa chừng: điện thoại đã gửi, máy chủ đã ghi, nhưng
phản hồi không về nên ứng dụng tưởng chưa gửi và gửi lại. Không có gì chặn thì một lần tick
thành hai dòng nhật ký.

**Các phương án đã cân nhắc.**
- Không chặn, chấp nhận bản ghi trùng.
- Đoán trùng theo nội dung (cùng nội dung trong một khoảng thời gian): phương án cân nhắc khi
  thiết kế, không có thử nghiệm trong repo. Cách này nhầm khi người dùng cố ý làm cùng một thao
  tác hai lần.
- Mỗi thao tác mang một mã do máy sinh ra (`client_action_id`), máy chủ nhớ mã đó.

**Lý do chọn.** Chọn mã riêng cho mỗi thao tác. Máy chủ lưu mã cùng dấu vân tay nội dung
(`request_hash`, bảng `field_client_actions`). Gửi lại cùng mã thì nhận lại kết quả cũ, không
tạo bản ghi mới. Cùng mã mà nội dung khác thì trả lỗi 409 "Mã thao tác đã được sử dụng", để một
mã không bị dùng cho hai việc. Ứng dụng di động giữ hàng đợi trong SecureStore (Keychain /
Android Keystore), bản web trong IndexedDB, và gửi theo đúng thứ tự từng ca.

**Ưu điểm và hạn chế.**
- Ưu: gửi lại bao nhiêu lần cũng an toàn. Có kiểm thử riêng, ví dụ
  `test_replayed_offline_edit_is_applied_once`, `test_offline_replay_creates_one_report_and_one_signature`,
  `test_concurrent_retry_with_the_same_action_id_replays_instead_of_500`.
- Hạn chế: tạo ca mới, thêm bước checklist và hỏi trợ lý vẫn cần mạng. Bản web chưa giữ sẵn dữ
  liệu ca đầy đủ khi mất sóng. Bảng `field_client_actions` hiện chưa có job dọn bản ghi cũ.

**Nếu làm tiếp sẽ đổi gì.** Thêm hạn giữ cho `field_client_actions`. Mở rộng chế độ ngoại tuyến
cho việc tạo ca và thêm bước checklist.

## 4. Kho tài liệu theo đội

**Vấn đề.** Tài liệu của một đội có thể là hợp đồng, sơ đồ nhà khách hàng, quy trình nội bộ.
Đội này không được thấy tài liệu của đội kia.

**Các phương án đã cân nhắc.**
- Một kho chung, lọc theo đội bằng điều kiện truy vấn: chỉ cần quên một điều kiện là lộ chéo.
- Mỗi đội một kho riêng.

**Lý do chọn.** Mỗi đội một collection riêng trong ChromaDB (`workspace_{id}`), tài liệu cá
nhân ở collection khác (`user_{id}`) — xem `_collection_name` trong `backend/rag/ingest.py`.
Khoá bộ nhớ đệm Redis cũng có `workspace_id` (`backend/rag/cache.py`), nên không có đường nào
đi từ câu hỏi của đội này sang tài liệu của đội kia. Kiểm thử:
`test_workspace_retrieval_isolated_from_personal_collections_and_cache`. Mọi truy vấn ca sửa
đều kiểm tra tư cách thành viên ở máy chủ, không chỉ ẩn nút trên giao diện.

**Ưu điểm và hạn chế.**
- Ưu: cách ly theo cấu trúc, không phụ thuộc vào việc lập trình viên nhớ thêm điều kiện lọc.
- Hạn chế: số collection tăng theo số đội. Tài liệu muốn dùng chung cho nhiều đội phải nạp lại
  vào từng đội.

**Nếu làm tiếp sẽ đổi gì.** Thêm kho tài liệu dùng chung cho cả tổ chức (chỉ đọc), tách khỏi kho
riêng của từng đội.

## 5. Quyền sửa tài liệu

**Vấn đề.** Kho cẩm nang là nguồn cả đội dựa vào để quyết định ngoài hiện trường. Một bản cũ bị
tải đè lên bản mới sẽ khiến nhiều người làm sai cùng lúc, và lỗi này khó phát hiện vì câu trả
lời vẫn có vẻ hợp lý, vẫn có trích nguồn.

**Các phương án đã cân nhắc.**
- Mọi thành viên đều được tải lên và xoá.
- Chỉ trưởng đội được tải lên và xoá; kỹ thuật viên chỉ đọc.
- Quy trình duyệt hai bước (người gửi, người duyệt): phương án cân nhắc khi thiết kế, chưa làm.

**Lý do chọn.** Chọn chỉ trưởng đội (`_require_workspace_owner` trong `backend/docs/router.py`).
Cách này đơn giản và đủ cho đội nhỏ, vì tài liệu được đọc thường xuyên nhưng ít khi phải sửa.

**Ưu điểm và hạn chế.**
- Ưu: một người chịu trách nhiệm cho kho; kỹ thuật viên đọc thoải mái.
- Hạn chế: trưởng đội vắng thì không ai cập nhật được. Chưa có lịch sử phiên bản tài liệu, nên
  tải nhầm bản cũ thì phải tải lại bản đúng bằng tay.

**Nếu làm tiếp sẽ đổi gì.** Giữ lịch sử phiên bản cho mỗi tài liệu và cho phép quay lại bản
trước; cân nhắc thêm vai trò phó đội có quyền ghi.

## 6. Độ bám nguồn theo từ vựng

**Vấn đề.** Cần biết câu trả lời có dựa vào tài liệu đã lấy về hay không. Cách phổ biến là gọi
chính mô hình AI chấm lại câu trả lời của nó: nhanh nhưng dễ dãi với chính mình.

**Các phương án đã cân nhắc.**
- Chỉ dùng AI chấm (LLM-as-judge).
- Người chấm toàn bộ: chính xác nhất nhưng không chạy được hằng ngày.
- Thêm một thước đo không dùng AI, đối chiếu từ ngữ.

**Lý do chọn.** Thêm thước đo từ vựng (`backend/rag/grounding.py`): tách câu trả lời thành từng
câu, đo tỉ lệ từ trùng với đoạn tài liệu; câu nào đạt ngưỡng 0,35 và không bị đảo nghĩa (kiểm
tra từ phủ định đơn giản) mới tính là có căn cứ. Chạy lại bao nhiêu lần cũng ra cùng kết quả,
dùng để soi xem điểm AI chấm có lạc quan quá không.

**Ưu điểm và hạn chế.**
- Ưu: rẻ, nhanh, tất định, không tốn lượt gọi AI.
- Hạn chế: trùng từ không chứng minh được đúng nghĩa; câu trả lời đúng từ khoá vẫn có thể sai số
  liệu. Chính mã nguồn ghi rõ giới hạn này. Cả thước từ vựng lẫn AI chấm chỉ là công cụ chẩn
  đoán, không thay người đánh giá (`docs/RAG-ROADMAP.md`).

**Nếu làm tiếp sẽ đổi gì.** Tăng phần holdout lên từ 100 câu trở lên do người nghiệp vụ duyệt.
Mã đã có trường `reviewed_by`, `reviewed_at`; hiện 0/12 câu holdout được duyệt.

## 7. Chấm điểm theo mẫu

**Vấn đề.** Mỗi lần AI chấm điểm là một lượt gọi AI nữa cho mỗi câu trả lời: gấp đôi chi phí và
thêm độ trễ, chỉ để theo dõi chất lượng.

**Các phương án đã cân nhắc.**
- Chấm mọi câu trả lời.
- Không chấm.
- Chấm ngẫu nhiên một tỉ lệ.

**Lý do chọn.** Chấm ngẫu nhiên, mặc định 10% (`JUDGE_SAMPLE_RATE=0.1` trong
`backend/config.py`), chỉnh được bằng biến môi trường, đủ để thấy xu hướng theo thời gian. Câu
từ chối vì thiếu tài liệu không được đưa đi chấm, và hết ngân sách AI thì bỏ qua bước chấm
(`test_refusals_are_not_sent_to_the_judge`,
`test_background_judge_is_skipped_when_the_budget_is_exhausted`).

**Ưu điểm và hạn chế.**
- Ưu: chi phí theo dõi chất lượng chỉ tăng khoảng 10%, người dùng không phải chờ.
- Hạn chế: với ít người dùng, 10% là rất ít mẫu, xu hướng sẽ dao động mạnh.

**Nếu làm tiếp sẽ đổi gì.** Lấy mẫu theo nhóm (ví dụ theo đội, theo loại câu hỏi) thay vì ngẫu
nhiên đều, để nhóm nhỏ không bị bỏ sót.

## 8. Webhook CRM/NMS ký HMAC, tự gửi lại

**Vấn đề.** Ca sửa thường bắt nguồn từ hệ thống khác (ticket CRM, cảnh báo NMS). Hai bên phải
trao đổi được mà không nhận nhầm yêu cầu giả mạo, không tạo ca trùng khi đối tác gửi lặp, và
không mất thông báo khi đối tác tạm lỗi.

**Các phương án đã cân nhắc.**
- FieldOps định kỳ hỏi CRM (polling): phương án cân nhắc khi thiết kế, không có thử nghiệm trong
  repo; tốn lượt gọi và chậm.
- Webhook gửi thẳng, không thử lại.
- Webhook có chữ ký, chống lặp, và hàng đợi gửi ra (outbox) có thử lại.

**Lý do chọn.** Chọn phương án thứ ba (`backend/field/integrations.py`).
- Vào: kiểm chữ ký HMAC; trùng `event_id` thì bỏ qua (ràng buộc duy nhất
  `uq_integration_event`); sự kiện có `updated_at` cũ hơn bản đã áp dụng thì bỏ qua với kết quả
  `stale_ignored`.
- Ra: ghi vào bảng `field_webhook_deliveries` rồi gửi; lỗi thì thử lại sau 30·2^(n−1) giây
  ±20% (khoảng 63,5 phút cho 8 lần); hết lượt thì vào dead-letter để người xử lý gửi lại trong
  giao diện. Lỗi 4xx (trừ 408, 429) là lỗi của chính yêu cầu nên vào dead-letter ngay.

**Ưu điểm và hạn chế.**
- Ưu: không mất sự kiện, không tạo ca trùng, đối tác lỗi tạm thời không làm hỏng FieldOps.
  Kiểm thử: `test_inbound_crm_ticket_lifecycle_is_signed_and_idempotent`,
  `test_two_dispatchers_never_send_the_same_delivery_twice`.
- Hạn chế: chưa có adapter cho một CRM/NMS cụ thể; hệ thống đối tác phải gửi đúng định dạng
  webhook chung.

**Nếu làm tiếp sẽ đổi gì.** Viết adapter cho một CRM thật đầu tiên khi có khách hàng thí điểm,
và cho xoay vòng khoá bí mật HMAC mà không làm gián đoạn.

## 9. Chạy gọn trên một máy miễn phí

**Vấn đề.** Sản phẩm cần chạy thật cho một đội nhỏ với chi phí 0 đồng/tháng, trên một máy
Oracle Cloud Always Free (ARM, 2 OCPU, 12 GB RAM — `deploy/cloud/README.md`).

**Các phương án đã cân nhắc.**
- Cơ sở dữ liệu ứng dụng: SQLite một tiến trình ghi (F2-A), hoặc PostgreSQL ngay từ đầu (F2-B).
  Chủ dự án chọn F2-A ngày 23/11/2026 (README, mục "Giới hạn kiến trúc đã chọn").
- Lập lịch job hằng ngày (KPI, kiểm tra chất lượng dữ liệu, dọn dữ liệu cũ, sao lưu): Apache
  Airflow, hoặc bộ lập lịch tối giản viết sẵn trong `backend/pipeline/jobs/runner.py`.

**Lý do chọn.**
- SQLite (WAL, chờ khoá 30 giây) và một tiến trình API là đủ cho đội nhỏ (dưới 50 người dùng).
  Mã migration đã hỗ trợ cả PostgreSQL, nên chuyển sang chỉ cần đặt `DATABASE_URL`.
- Bộ lập lịch tối giản thay Airflow trên máy miễn phí, vì Airflow tốn khoảng 1,5 GB RAM. Bộ lập
  lịch tự chạy bù nếu lần chạy cuối đã quá 26 giờ. DAG Airflow (`airflow/dags/daily_rag_ingest.py`)
  vẫn giữ làm phương án thay thế; không được bật cả hai cùng lúc.

**Ưu điểm và hạn chế.**
- Ưu: chi phí 0 đồng, ít thành phần để vận hành, sao lưu đơn giản (một file SQLite và chỉ mục
  Chroma).
- Hạn chế: không chạy được nhiều bản API song song hay nhiều máy dùng chung file SQLite (ghi đồng
  thời sẽ lỗi `database is locked`); chỉ mục BM25, bộ đếm giới hạn tần suất và bộ nhớ đệm nằm
  trong bộ nhớ từng tiến trình. Theo `docs/TRIEN_KHAI_VA_BAO_TRI.md`, bước triển khai thật chưa
  làm: các việc cần tài khoản của chủ dự án (Oracle, DuckDNS, Google, Expo) còn chờ.

**Nếu làm tiếp sẽ đổi gì.** Khi vượt một đội nhỏ: chuyển sang PostgreSQL và đưa trạng thái trong
bộ nhớ sang Redis, ước lượng 10–15 người-ngày (README).
