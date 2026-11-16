# Hệ thống 3: RAG và trợ lý AI — ingest, truy hồi, sinh câu trả lời, trích dẫn, chi phí, đánh giá

**Phạm vi mã nguồn:**

- `backend/rag/`, gồm cả `rag/technical/`
- `backend/chat/`
- `backend/docs/router.py`
- `backend/costs/`
- `backend/saas/limits.py`
- `backend/evals/`

---

## 1. Vai trò của hệ thống

Trợ lý chỉ trả lời dựa trên tài liệu mà người dùng hoặc đội đã upload. Mỗi câu trả lời được lưu kèm một **bundle bằng chứng**: các đoạn trích, SHA-256 của chúng, phiên bản tài liệu và revision của collection. Một câu trả lời chỉ được lưu khi mọi nhãn `【E#】` trong đó trỏ tới đoạn trích có thật.

---

## 2. Luồng xử lý

### 2.1 Ingest tài liệu

Request đi theo `docs/router.py:upload_doc` → `rag/ingest.py` → `document_lifecycle.py`:

1. **Kiểm tra quyền:** tài liệu cá nhân, hoặc là owner nếu tài liệu thuộc workspace.
2. **Kiểm tra tệp:**
   - tên qua `safe_filename`, và thư mục cha sau `resolve()` phải đúng thư mục upload;
   - phần mở rộng phải được cho phép;
   - kích thước tối đa 20 MB.
3. **Ngân sách AI (U7, đã sửa):** `enforce_budget` chạy trước khi tốn tiền embedding hoặc OCR.
4. **Nạp và chia đoạn:**
   - PDF, DOCX và TXT dùng loader LangChain; ảnh được OCR bằng Gemini (khoảng 258 token mỗi ảnh).
   - `RecursiveCharacterTextSplitter(1000, 100)`. Số chunk xấp xỉ $\lceil (L-100)/900\rceil$; ví dụ L = 2500 cho **3 chunk**.
5. **Embedding:** theo lô 20, nghỉ 1,5 s giữa các lô. Gặp 429 thì chờ thời gian server báo cộng 2 s, tối đa 120 s.
6. **Publish:** ghi journal và snapshot, ghi vào Chroma collection `user_N` hoặc `workspace_N`, rồi tăng revision. Khoá luôn lấy theo thứ tự source → collection. Lúc khởi động, `recover_pending` hoàn tác hoặc hoàn tất publish còn dở.

### 2.2 Một lượt chat SSE

**Bước 1: Cổng vào (`chat/router.py`)**

- Câu hỏi dài **1–4000 ký tự** (F6). Ô nhập trên web, embed và mobile cũng giới hạn 4000.
- Hội thoại phải thuộc người dùng.
- `enforce_budget`, sau đó `enforce_query_quota`:

$$used' = used + 1 \text{ chỉ khi } used < limit$$

- Một câu `UPDATE` có điều kiện được commit ngay. Giới hạn: Free 50 câu/ngày, Pro 1000 câu/ngày (theo ngày UTC).

**Bước 2: Lịch sử đưa vào prompt (`prompt.format_history`, F7)**

- Chỉ giữ **10 lượt gần nhất**, mỗi lượt tối đa 2000 ký tự.
- Xoá mọi nhãn `【…】` hoặc `［…］` cũ.
- Tổng phần lịch sử tối đa khoảng 20.000 ký tự, tức khoảng 5.000 token.

**Bước 3: Truy hồi (`retriever.retrieve`)**

- `final_k = 5`, số ứng viên = `max(15, k)`.
- Hai nhánh vector và BM25 chạy song song trên 8 worker. Nếu một nhánh rỗng, dùng nhánh còn lại mà không fuse.

**BM25L** (k₁ = 1,5, b = 0,75, δ = 0,5):

$$idf = \ln(N+1)-\ln(df+0{,}5)$$

$$c=\frac{tf}{1-b+b\,|d|/\overline{|d|}}$$

$$s=\sum idf\cdot tf\cdot\frac{(k_1+1)(c+\delta)}{k_1+c+\delta}$$

Ví dụ: N = 10, df = 2, tf = 2, $|d|$ bằng độ dài trung bình. Khi đó idf = 1,4816, c = 2, và đóng góp của từ = 1,4816 × 3,125 ≈ **4,63**.

**RRF:**

$$RRF(d)=\sum_i 1/(60+r_i)$$

Ví dụ: chunk hạng 1 ở nhánh vector và hạng 3 ở BM25 được 0,032266; chunk chỉ có hạng 2 ở nhánh vector được 0,016129.

**Rerank:**

- Cohere nếu được cấu hình.
- Không có thì dùng lexical: $4|Q\cap H| + 2|Q\cap B| + |C\cap H|$. Ví dụ: Q = {pon, đỏ, ont}, tiêu đề chứa "pon", thân chứa "pon" và "đỏ" cho s = 4 + 4 = **8**.

**Cache:** Redis, TTL 300 s. Khoá chứa scope, k, source và `revision:query`, nên publish mới tự làm cache cũ không còn được dùng.

**Bước 4: Prompt và bằng chứng (`evidence_context`, F8)**

- Mỗi chunk thành một khối `【E#】 Nguồn: …`.
- Trong bản đưa vào prompt, `【】` và `［］` của tài liệu bị đổi thành `[]`, và dòng `---` bị vô hiệu. Tài liệu upload không thể tự tạo khối bằng chứng giả.
- Quote lưu trữ và SHA-256 giữ nguyên văn.

**Bước 5: Sinh và stream câu trả lời (`chain.stream_rag_answer`)**

- Giới hạn: 8 slot đồng thời, hàng đợi 16, mỗi frame tối đa 512 ký tự.
- **Kiểm tra nhãn ngay trong lúc stream (D3, đã sửa):**
  - Văn bản từ một ngoặc mở `【` hoặc `［` chưa đóng được giữ lại; tối đa 64 ký tự, và không giữ qua xuống dòng.
  - Trước khi phát một đoạn văn bản, code chạy `audit_citations` trên toàn bộ phần đã phát cộng đoạn sắp phát.
  - Gặp nhãn không tồn tại thì dừng ngay và gửi frame `error`, nên người dùng **không bao giờ thấy** nhãn bịa.
  - Câu trả lời hợp lệ vẫn stream gần như tức thời; chỉ phần nhãn bị trễ vài token.
- **Citation audit:**
  - `invalid`: có nhãn dạng `E…` không khớp **chính xác** một mục evidence (gồm cả `【 E9】`, `【e1】`, `［E7］`, U17).
  - Ngược lại: `cited`, `uncited` hoặc `no_evidence`.
  - Mã thiết bị ASCII như `[E204]` không bị coi là nhãn.
- **Lưu câu trả lời:** chỉ khi audit không `invalid`. Judge chỉ lấy mẫu câu trả lời **có context** (F15), với xác suất p = 0,1.

### 2.3 Đo chi phí (`costs/`, `rag/llm.py`)

$$cost=\frac{in\cdot p_{in}+(out+thinking)\cdot p_{out}}{10^6}+units\cdot p_{unit}$$

Ví dụ: `gemini-2.5-flash`, 3000 token vào và 400 token ra cho 0,0009 + 0,0010 = **0,0019 USD**.

- **Stream bị ngắt:**
  - Gemini: ghi `partial`; khi chưa có số token đầu ra thì ước lượng $\lceil len/4\rceil$ (F16).
  - Ollama: giờ cũng ghi đúng một bản ghi `partial` (F17).
- **Ngân sách ngày:** áp dụng cho chat, embed, `/v1/ask`, gợi ý câu hỏi (F5) và upload (U7). Đây là **trần mềm**: N request đồng thời cùng qua cổng kiểm tra trước khi chi phí kịp được ghi.

### 2.4 Bộ đánh giá offline và live (`evals/`)

- **Grader, F/U14 (đã sửa):**
  - Một cụm có từ phủ định đứng ngay trước ("không được tắt nguồn") **không** còn được tính là đã nêu dữ kiện "tắt nguồn", và cũng không bị tính là nội dung cấm. Chỉ nhóm "không …" được coi là phủ định, vì sau khi bỏ dấu thì "đừng/đúng", "chưa/chứa", "cấm/cầm" trùng chữ nhau.
  - Câu trả lời có trích `【E#】` không bị tính là lời từ chối.
- **Harness:**
  - Judge dùng `max_tokens = 1024` và clamp điểm vào [0, 1], giống judge production.
  - Báo lỗi nếu `k > TOP_K`.
- **Chỉ số:** recall, precision, MRR, nDCG@k với relevance nhị phân.

  Ví dụ nDCG: có 2 nguồn đúng ở hạng 1 và 3, k = 5. DCG = 1 + 1/log₂4 = 1,5; IDCG = 1 + 1/log₂3 = 1,6309; nDCG = **0,9197**.

---

## 3. Lỗi đã sửa

| ID | Mức | Vị trí | Kịch bản lỗi trước khi sửa | Test |
|---|---|---|---|---|
| D3 | TB | `rag/chain.py` | Nhãn bịa `【E999】` được stream và hiển thị đầy đủ rồi mới báo lỗi. | `round2::test_invented_citation_label_is_never_streamed` (2 biến thể), `round2::test_valid_label_split_across_tokens_still_streams_the_whole_answer` |
| F8 | TB | `rag/evidence.py:evidence_context` | Tài liệu chứa `---` cùng `【E2】 Nguồn: …` giả được một khối bằng chứng. | `test_document_text_cannot_forge_an_evidence_block` |
| F7 | TB | `rag/prompt.py` | Lịch sử không giới hạn; nhãn cũ bị chép lại và được tính là trích dẫn hợp lệ. | `test_history_is_bounded_and_drops_stale_citation_labels` |
| F6 | TB | `chat/schemas.py` | Câu hỏi 1 MB chỉ tốn 1 quota nhưng tốn hàng trăm nghìn token. | `test_chat_question_has_the_same_length_cap_as_embed_and_api` |
| F5 | TB | `docs/router.py` | Gợi ý câu hỏi gọi Gemini mà không chịu ngân sách. | `test_suggested_questions_respect_the_daily_ai_budget` |
| U7 | Thấp | `docs/router.py:upload_doc` | Upload (embedding, OCR) không chịu ngân sách. | `round2::test_document_upload_respects_the_daily_ai_budget` |
| U17 | Thấp | `rag/evidence.py:audit_citations` | `【 E9】`, `【e1】`, `［E7］` bị bỏ qua, nên audit báo `uncited` thay vì `invalid`. | `round2::test_malformed_citation_labels_are_invalid_not_uncited` |
| F15 | Thấp | `chat/router.py` | Câu từ chối không có context được judge chấm 1,0, làm thổi phồng điểm chất lượng. | `test_refusal_without_context_is_not_sampled_for_the_judge` |
| F16 | Thấp | `rag/llm.py:_record_gemini` | Stream bị ngắt ghi số token đầu ra = 0. | `test_interrupted_gemini_stream_still_meters_generated_output` |
| F17 | Thấp | `rag/llm.py:stream_text` | Ollama không ghi usage khi client ngắt kết nối. | `test_ollama_stream_closed_by_client_is_metered_once` |
| U14 | Thấp | `evals/graders.py`, `harness.py` | Dữ kiện bị phủ định vẫn "đạt"; câu bịa kèm lời rào đón được tính là từ chối đúng; judge thiếu clamp; `k` không có tác dụng. | `round2::test_negated_fact_…`, `round2::test_cited_answer_with_a_hedge_…`, `round2::test_eval_judge_scores_…` |

---

## 4. Quyết định giữ nguyên và hạn chế còn lại

- **D4:** quota bị trừ khi provider lỗi, để chống lạm dụng.
- **D5:** `rag/technical` từ chối trả lời khi có hơn 4 parent đạt ngưỡng, thay vì cắt bớt (ưu tiên an toàn kỹ thuật).
- **Prompt chưa tách vai trò:** rule, context và câu hỏi vẫn nằm chung một chuỗi `contents`, chưa dùng `system_instruction` của Gemini. Phòng thủ prompt injection gồm câu dặn trong system prompt và việc vô hiệu nhãn (F8).
- **Audit chỉ kiểm danh tính nhãn:** `audit_citations` không kiểm tra nội dung có đúng nghĩa với đoạn trích (`semantic_support_verified = False`).
- **Nhãn quá dài:** nhãn dài hơn 64 ký tự có thể đã được phát một phần trước khi đóng ngoặc. Audit cuối vẫn chặn việc lưu câu trả lời đó.
- **Ngân sách là trần mềm** (§2.3).

---

## 5. Vùng đã xác minh là đúng

- RRF dùng hạng tính từ 1, và khoá dedupe thống nhất giữa hai nhánh.
- Cô lập tenant theo collection; cache khoá theo scope và revision.
- BM25 không bị lệch so với Chroma nhờ revision file.
- Vòng đời publish và recovery chạy lại được nhiều lần (idempotent).
- Slot stream được giải phóng đúng một lần.

---

## 6. Kiểm thử

| Bộ test | Kết quả |
|---|---|
| `test_rag_*.py`, `test_bm25_retrieval.py`, `test_rerank.py`, `test_technical_rag.py`, `test_evals.py` | PASS |
| `run_evals.py --mode offline` (68 câu) | pass 0,7206, recall@5 1,0, MRR 0,9121, 0 vi phạm. **Giống hệt trước khi sửa:** grader mới không đổi kết quả trên bộ dữ liệu hiện có |
| `run_eval_benchmark.py --offline` | recall 1,0, MRR 1,0 |
| `run_rag_evaluation.py --mode validate-only` | validated |
| Gemini, Cohere, Ollama thật | **Chưa gọi** (chỉ dùng provider giả lập) |
