# FieldOps — Đánh giá đa vai trò và thuật toán (vòng 5)

- **Ngày:** 2026-11-17
- **Nhánh:** `fullstack/fieldops-production`
- **Mốc so sánh:** `ddf8352`
- **Phạm vi:** backend (FastAPI), frontend (Next 16), mobile (Expo) và cấu hình deploy.
- **Cách làm:** đọc mã và chạy thật (API cục bộ + trình duyệt).
- **Tệp không đụng tới:** `.env`, `TAI-KHOAN.md`, `evaluation.local`, `*.docx` của người dùng. Không in bí mật nào.

Mỗi lỗi đã sửa có ít nhất một test hồi quy trong `tests/test_audit_regressions_round5.py`. Các test này **fail trên `ddf8352`**: 13/14 fail. Test còn lại pass trên cả hai bản, và đó là chủ đích: nó xác nhận DOCX hợp lệ vẫn đọc được bình thường.

---

## 1. Product / CEO

**Sản phẩm làm tốt điều gì?**

FieldOps giải đúng một nỗi đau cụ thể: kỹ thuật viên FTTH/viễn thông ở hiện trường cần
1. biết **ca nào làm trước**;
2. tra **đúng SOP theo model thiết bị**;
3. ghi lại kết quả kể cả khi **mất sóng**.

Luồng "tổng đài/CRM → ca sửa → điều phối → trợ lý theo SOP → checklist offline" là liền mạch. QA chạy thật đã đi trọn luồng này (mục 6).

**Thiếu so với nhu cầu thực tế** (theo thứ tự ưu tiên):

| # | Khoảng trống | Bằng chứng | Tác động |
|---|---|---|---|
| 1 | Mobile không có màn hình "Lịch của tôi hôm nay"; app không gọi `/schedule`. Không có push notification. | `mobile/` không có lời gọi tới schedule | Thợ không biết ca kế tiếp nếu không mở web |
| 2 | Không có biên bản đóng ca (mã kết quả, chữ ký khách). Phần ghi ngược lên CRM rất mỏng. | `field/integrations.py` chỉ gửi trạng thái | Không đủ để nghiệm thu với nhà mạng |
| 3 | Không sửa được thông tin ca sau khi tạo. Không có geocoding, nên toạ độ phải nhập tay. | `field/router.py` chỉ có patch dispatch/status | Điều phối phụ thuộc người nhập toạ độ |
| 4 | Quota tính theo user, không theo đội/ghế. Chưa định giá gói Pro ở đâu cả. | `saas/limits.py`, `billing/` | Chưa có mô hình doanh thu B2B rõ |
| 5 | Một số phần làm quá sớm so với giai đoạn: medallion/Airflow, CLI technical-RAG, widget nhúng + Public API, nhiều biến thể deploy. | `pipeline/`, `airflow/`, `deploy/*` | Tốn công bảo trì |

**Khuyến nghị 90 ngày:**
1. Mobile "Lịch hôm nay" và push.
2. Biên bản đóng ca và ghi ngược CRM.
3. Geocoding địa chỉ và SLA.
4. Thử nghiệm thực địa phần offline và trợ lý với một đội thật.
5. Định giá theo đội, và cắt bớt các nhánh deploy không dùng.

---

## 2. Thuật toán / ML

### 2.1 Bảng thuật toán

| Thuật toán | Dùng ở đâu | Đánh giá | Lý do | Khuyến nghị |
|---|---|---|---|---|
| Chia chunk RecursiveCharacterTextSplitter 1000/100 ký tự | `rag/ingest.py`, `config.py:81-82` | Tạm phù hợp | Mặc định hợp lý cho SOP. Corpus eval chỉ khoảng 16 chunk, nên lấy 15 ứng viên gần như là lấy cả kho; tham số chưa từng được kiểm. | Tách theo tiêu đề mục trước rồi mới cắt. Dò lại tham số trên corpus lớn hơn ≥10 lần. |
| Embedding `gemini-embedding-001` | `config.py:17`, `ingest.py` | Phù hợp | Đa ngôn ngữ, tốt với tiếng Việt, có phân loại task truy vấn/tài liệu. | Giữ. |
| BM25L (k1 1.5, b 0.75, δ 0.5), tách theo âm tiết | `rag/bm25.py` | Tạm phù hợp → **đã sửa một phần** | Tách theo âm tiết là baseline chấp nhận được. Trước đây **không chuẩn hoá NFC**, nên văn bản NFD bị cắt vụn (đã sửa). Chưa gộp dạng không dấu, nên gõ "loi mat nguon" không khớp. | Thêm token bỏ dấu; cân nhắc tách từ (pyvi/underthesea). |
| Cổng khớp mã kỹ thuật (E-999 ≠ E-204) | `bm25.py:144-159` | Phù hợp | Tránh trả SOP sai mã lỗi. | Gộp các dạng "E204", "E 204", "E-204". |
| Gộp RRF k=60 | `bm25.py`, `retriever.py:294` | Phù hợp | Công thức chuẩn. Id chunk nhất quán giữa hai nhánh. | Giữ. |
| TOP_K 5 / 15 ứng viên | `config.py:86-88` | Tạm phù hợp | Chưa có căn cứ thực nghiệm. | Quét tham số trên corpus lớn. |
| Cohere rerank-multilingual-v3.0 | `rag/rerank.py` | Phù hợp | Đa ngôn ngữ; lỗi thì lùi về cách khác an toàn. | Giữ. |
| Rerank từ vựng (khi không có Cohere) | `rerank.py:47-70` | **Chưa phù hợp** | Đếm từ trùng, không có IDF, nên vứt bỏ thứ tự RRF (mất tín hiệu vector). Một số heuristic được chỉnh theo chính bộ golden. | Khi không có Cohere thì giữ nguyên thứ tự RRF, hoặc gộp bằng RRF. |
| Cache truy hồi | `rag/cache.py` | Phù hợp | Khoá theo revision của kho, không bao giờ trả kết quả cũ. | Có thể chuẩn hoá NFC câu hỏi trước khi làm khoá. |
| Technical RAG: FTS5 với token hex | `rag/technical/store.py` | Tạm phù hợp → **đã sửa NFC** | An toàn trước injection FTS. "204" không khớp "E-204". | Đánh chỉ mục cả phần con của mã. |
| Technical RAG: `min_score` | `technical/pipeline.py` | Tạm phù hợp | Thiết kế đúng (bắt buộc có `calibration_id`) nhưng chưa có bộ hiệu chuẩn thật. | Hiệu chuẩn trên các cặp có nhãn. |
| Technical RAG: `max_parents` | `pipeline.py:118-121` | Cần quyết định | Nhiều bằng chứng liên quan quá thì **từ chối trả lời** thay vì lấy top-N. Có thể là chủ đích ("phải đưa nguyên đoạn"). | Mục 8, cần chủ sản phẩm quyết định. |
| Technical RAG: dấu phẩy thập phân | `pipeline.py:41` | Cần quyết định | "12,5 V" luôn bị từ chối vì "dấu phân cách mơ hồ". Tiếng Việt dùng dấu phẩy thập phân. Đây là quyết định an toàn có chủ đích. | Mục 8. |
| Kiểm trích dẫn 【E#】 | `rag/evidence.py` | Phù hợp (kiểm toàn vẹn) | Chỉ kiểm nhãn có tồn tại, không kiểm nội dung có hỗ trợ câu hay không. | Ghi rõ giới hạn trong tài liệu. |
| Grounding từ vựng, ngưỡng 0.35 | `rag/grounding.py` | Tạm phù hợp → **đã sửa NFC** | Được ghi rõ là chẩn đoán. Ngưỡng chưa hiệu chuẩn. | Báo cáo tỉ lệ thô, bỏ ngưỡng cứng. |
| LLM judge lấy mẫu 10% | `chat/router.py:42`, `rag/judge.py` | Tạm phù hợp | Gemini tự chấm Gemini. Câu từ chối được gán faithfulness 1.0, làm đẹp số trung bình. | Loại câu từ chối khỏi trung bình; đối chiếu với nhãn người. |
| Bộ chấm eval: phủ dữ kiện / nội dung cấm | `evals/graders.py` | Tạm phù hợp → **đã sửa phủ định** | "Không được **phép** tắt nguồn" từng bị tính là có dữ kiện "tắt nguồn". Cảnh báo an toàn bị tính là vi phạm. Ngưỡng đạt ≥0.5 dễ dãi. | Bắt buộc đủ các dữ kiện then chốt. |
| Bộ chấm groundedness 0.6 | `graders.py` | Tạm phù hợp → **đã sửa** | "khong" là stopword, nên câu trả lời đảo nghĩa tài liệu vẫn đạt 100%. | Đã thêm kiểm tra đảo cực. |
| nDCG / MRR / recall / precision | `graders.py:127-144` | Phù hợp | IDCG đúng; MRR tính trên nguồn duy nhất. Precision chia cho số nguồn, không chia cho k (ghi rõ). | Đo ở mức chunk có nhãn. |
| Percentile | `graders.py` và `pipeline/jobs/tasks.py` | Tạm phù hợp | Eval dùng nội suy tuyến tính, KPI dùng nearest-rank, nên p95 hai nơi lệch nhau. | Dùng một định nghĩa. |
| Ngưỡng/baseline eval | `evals/thresholds.json`, `baselines/` | **Chưa phù hợp** | **Rò rỉ dữ liệu:** baseline chốt trên split "all" gồm cả holdout. Holdout chỉ 12 câu (±1 câu = ±8 điểm), trong khi dung sai 0.03 nhỏ hơn độ phân giải đó. Nhãn do dev tự viết. | Đóng băng holdout; tăng lên ≥100 câu do chuyên gia duyệt; báo cáo khoảng tin cậy. |
| Provider offline (hashing embedding + trả lời trích xuất) | `evals/offline_providers.py` | Tạm phù hợp | Tốt làm kiểm thử pipeline. Vô nghĩa làm thước đo chất lượng: groundedness luôn 1.0. | Gọi là "pipeline gate", không trích dẫn như chất lượng. |
| Điều phối VRPTW (haversine×1.35 @30 km/h, chèn rẻ nhất, 2-opt, trọng số khẩn cấp) | `field/dispatch.py` | Tạm phù hợp | Đủ cho khoảng 5 thợ / 50 ca (QA: 3 ca xếp trong 53 ms, ca khẩn lên đầu tuyến). Chỉ tối ưu trong một tuyến (không chuyển ca giữa các tuyến), nên có thể bỏ sót ca dù tồn tại lịch khả thi. Không có quay về base, kỹ năng, giờ nghỉ. | Thêm relocate/swap liên tuyến; trên 50 ca thì dùng OR-Tools; cân nhắc OSRM. |
| Đặt trước ngân sách AI | `costs/budget.py` | Phù hợp | Đặt trước theo chi phí xấu nhất, kiểm tra trong khoá. | Ghi rõ ngày reset theo UTC (07:00 giờ VN). |
| Backoff webhook | `field/integrations.py:450` | Tạm phù hợp | Lần thử lại đầu là 60 s, không phải 30 s. Không có jitter. Lỗi 4xx vĩnh viễn vẫn bị thử 8 lần. | Thêm jitter; 4xx (trừ 408/429) thì cho dead ngay. |
| Rate limiter cửa sổ trượt | `auth/ratelimit.py` | Phù hợp | Đúng; giới hạn theo tiến trình (đã ghi trong tài liệu). | Giữ. |
| KPI ngày | `pipeline/jobs/tasks.py` | **Sai → đã sửa** | "Ca hoàn tất" được đếm theo `updated_at`, nên sửa ca hôm sau làm ca nhảy ngày. | Đã thêm cột `resolved_at` (migration 6). |
| Kiểm chất lượng dữ liệu kiểu Great Expectations | `validation/ge_validate.py` | Tạm phù hợp | Chỉ một chunk trùng hoặc chunk <10 ký tự (vd. "Trang 3") làm hỏng cả file. | Bỏ hoặc gộp chunk rác kèm cảnh báo. |

### 2.2 Đánh giá chung

Về kiến trúc, phần truy hồi lai BM25 + vector + RRF + rerank là lựa chọn đúng cho SOP tiếng Việt có nhiều mã kỹ thuật.

Điểm yếu thật nằm ở **phương pháp đánh giá**: corpus quá nhỏ, holdout bị rò, nhãn do dev tự viết. Vì vậy các con số eval hiện chỉ chứng minh pipeline chạy đúng, **chưa chứng minh chất lượng**.

---

## 3. Staff engineer

**Điểm tốt:**
- Ranh giới module rõ (auth / rag / field / costs / pipeline).
- Migration có sổ phiên bản và khoá.
- Outbox webhook giành quyền gửi bằng UPDATE có điều kiện.
- Offline outbox có idempotency.
- Test phủ rộng (số liệu ở mục 9).

**Rủi ro vận hành (chưa sửa; lý do ở mục 8):**
1. `enforce_budget` (ghi SQLite có khoá) chạy thẳng trong handler `async`. Một lần chờ khoá có thể đóng băng worker duy nhất tới 30 s, kể cả `/health` (`chat/router.py:211`, `embed/router.py:245`, `docs/router.py:62,185`).
2. Dispatcher webhook dùng `httpx` timeout theo từng lần đọc. Một receiver "nhỏ giọt" có thể giữ dispatcher quá `CLAIM_LEASE` (5 phút), nên bị gửi trùng và các đội khác bị kẹt (`field/integrations.py:108,465`).
3. Rò tài nguyên SSE:
   - Client ngắt trước byte đầu thì giữ chỗ ngân sách đến hết TTL 15 phút.
   - 8 `_STREAM_SLOTS` chỉ được nhả khi luồng provider đọc xong (tới 60 s).
   - Kiểm trích dẫn chạy O(n²) theo token.
4. Lần đăng nhập đầu chạy song song có thể tạo hai "Đội kỹ thuật" mặc định (`field/router.py:276-287`).
5. Giới hạn input tính theo ký tự/4, đếm thiếu token tiếng Việt có dấu (khoảng 2–3 ký tự/token), nên trần chi phí một request có thể bị vượt (`rag/llm.py:70`).
6. Caddy `encode zstd gzip` có thể nén luồng SSE (`deploy/cloud/Caddyfile:6`); cần kiểm bằng curl.

---

## 4. Security

**Đã sửa:**

| Mức | Lỗi | Sửa | Test |
|---|---|---|---|
| Cao | **DOCX zip bomb.** `docx2txt` giải nén cả `word/document.xml` vào RAM. File 20 MB có thể nở ~20 GB, làm OOM container API 2 GB; bất kỳ user nào cũng làm được. | Kiểm `infolist()` trước khi đọc: tổng giải nén ≤50 MB, tỉ lệ ≤100. Trả 400 với thông điệp rõ (`rag/ingest.py`, `docs/router.py`). | `test_docx_zip_bomb_is_rejected_before_extraction`, `test_normal_docx_still_loads` |
| Trung bình | OCR Gemini không có trần output, trong khi ngân sách giả định có. | Thêm `max_output_tokens=LLM_MAX_OUTPUT_TOKENS` (`rag/llm.py`). | `test_ocr_call_caps_output_tokens` |
| Thấp | `GET /documents/suggested-questions` tốn tiền LLM nhưng không cần header CSRF. Trang lạ có thể mở link lặp lại để đốt ngân sách AI ngày của nạn nhân. | Dependency `require_csrf_header` (`dependencies.py`). Frontend vốn đã gửi header này. | `test_suggested_questions_requires_csrf_header_for_cookie_sessions` |
| Thấp (deploy) | Upload dùng thẳng `NEXT_PUBLIC_API_URL`. Render truyền host không có scheme, nên URL upload thành tương đối và hỏng. | Dùng `API_URL` đã chuẩn hoá (`frontend/lib/api.ts`, `UploadZone.tsx`). | typecheck + build |

**Còn lại (cần quyết định; xem mục 8):**
- **Cao (deploy):** blueprint Render đặt web và api trên hai subdomain `onrender.com`. Đây là Public Suffix, nên hai bên khác site và cookie SameSite=Lax không được gửi. Đăng nhập xong sẽ quay vòng về /login. Cần proxy cùng origin hoặc domain riêng.
- **Thấp–TB:** `/auth/session` gia hạn token vô hạn; logout không thu hồi token (không tăng `token_version`).
- **Thấp:** di trú token cũ xoá token trước khi `/auth/session` thành công, nên mở PWA khi offline bị đăng xuất.

**Đã kiểm và đúng:**
- CSRF phủ mọi route ghi.
- Không có login CSRF.
- CORS theo origin chính xác.
- CSP dùng nonce + `strict-dynamic`.
- Webhook vào: HMAC + giới hạn lệch giờ + giới hạn kích thước. Webhook ra: ghim IP, chặn SSRF, không theo redirect.
- Stripe webhook kiểm chữ ký trên byte thô.
- Embed token buộc preflight.

---

## 5. Designer / UX

Chấm trên bản build production, 1280×820 và 375×812, đăng nhập bằng tài khoản QA.

| Màn hình | Điểm | Lý do |
|---|---|---|
| `/field` — Ca hiện trường | **8/10** | Thẻ KPI, hàng đợi ca rõ, nhãn trạng thái tiếng Việt. Trên mobile 375 px xếp một cột tốt. Hơi dày thông tin ở chi tiết ca. |
| `/field/schedule` — Điều phối | **8/10** | Bản đồ và tuyến hiển thị dưới CSP. Báo rõ ca thiếu toạ độ. Thiếu kéo-thả để chỉnh tuyến. |
| `/field/integrations` | **7/10** | Rõ ràng, có secret và URL nhận. Môi trường dev hiện `localhost` trong URL nhận (đúng với dev). Thiếu nút "gửi thử". |
| `/chat` — Trợ lý tài liệu | **7/10** | Trạng thái rỗng tốt, hai nút hành động rõ. Người dùng đã đăng nhập vào `/` bị đưa về `/chat` chứ không phải `/field`, dù sản phẩm chính là ca hiện trường. |
| `/docs` — Tài liệu | 5 → **8/10 (đã sửa)** | Trước đây trang ghi "tài liệu của đội" nhưng thực ra nạp vào **kho cá nhân**, nên SOP tải lên ở đây không bao giờ tới được trợ lý trong ca. Đã đổi tên thành "Tài liệu cá nhân" (tiêu đề, thanh trên, thanh bên) và thêm ghi chú dẫn tới "Ca hiện trường → Tri thức dùng chung". |
| `/analytics` | **6/10** | Số liệu đúng. Lời dẫn mang thuật ngữ kỹ thuật ("Usage trong app RAG — không phải Grafana"). Biểu đồ 14 ngày rỗng thì chỉ là các thanh xám. |
| `/settings` | **7/10** | Gọn. Có báo rõ khi thanh toán chưa bật. |
| `/admin` | **6/10** | Bảng user rõ. Lẫn tiếng Anh ("Tổng users", "Role", "Docs", "Messages"). Xoá user chỉ có icon. |
| `/admin/costs` | **7/10** | Bộ lọc 7/30/90 ngày, nói rõ đang ở free tier nên $0. Khi chưa có dữ liệu thì để trống nhiều. |
| Mobile web 375 px | **8/10** | Menu hamburger; nội dung một cột, không cuộn ngang. |

Không chấm lại `/login` và `/embed` bằng ảnh ở vòng này, vì phiên trình duyệt đang đăng nhập nên `/login` tự chuyển trang. Hai màn này không thay đổi kể từ vòng trước.

---

## 6. QA (chạy thật)

**Môi trường:**
- API uvicorn trong WSL với DB SQLite tạm, `LLM_PROVIDER=gemini` và **không có khoá**, để mô phỏng mô hình chưa cấu hình.
- Next production build ở cổng 3311.
- Script HTTP thật (`httpx`, chế độ cookie).

| # | Luồng | Kết quả |
|---|---|---|
| 1–2 | Đăng ký/đăng nhập chủ đội và kỹ thuật viên (cookie httpOnly) | PASS |
| 3 | POST bằng cookie mà thiếu `X-Requested-With` bị 403 | PASS |
| 4 | Tạo đội | PASS |
| 5 | Mời kỹ thuật viên bằng email khác hoa/thường | PASS |
| 6 | Tạo 3 ca kèm toạ độ và ưu tiên | PASS |
| 7 | Kỹ thuật viên **không** sửa được dispatch (403) | PASS |
| 8 | Kỹ thuật viên nhận ca chưa giao | PASS |
| 9 | Gửi lại thao tác offline thì chỉ áp dụng đúng 1 lần | PASS |
| 10 | Lập lịch xếp đủ 3 ca (53 ms) | PASS |
| 11 | Ca khẩn được xếp đầu tuyến | PASS |
| 12 | Áp dụng lịch | PASS |
| 13 | Webhook CRM có chữ ký thì tạo ca | PASS |
| 14 | Chữ ký sai bị 401 | PASS |
| 15 | Trợ lý ca khi truy hồi lỗi trả **503 JSON** | **FAIL → sửa → PASS** |
| 16 | Trạng thái gói cước | PASS |
| 17 | Đăng xuất xoá cookie, sau đó `/auth/me` trả 401 | PASS |

**Kết quả: 16/17 trước khi sửa, 17/17 sau khi sửa.**

Lỗi ở #15: `answer_rag_once` không bọc lỗi truy hồi (embedding/Chroma). `/field/cases/{id}/assist` và `/v1/ask` vì thế trả **500 trần**, và request kế tiếp trên cùng kết nối keep-alive bị ngắt. Đã sửa: lỗi được bọc thành `RagGenerationError`, trả 503 công khai, không lộ chi tiết (`rag/chain.py`). Test: `test_retrieval_failure_returns_public_503[api|field]`.

---

## 7. Những gì đã sửa trong vòng này

| Lỗi | Tệp | Test hồi quy (fail trên `ddf8352`) |
|---|---|---|
| Truy hồi lỗi thành 500 trần ở trợ lý ca và `/v1/ask` | `backend/rag/chain.py` | `test_retrieval_failure_returns_public_503[api]`, `[field]` |
| Văn bản tiếng Việt NFD không khớp ở BM25, rerank, grounding, FTS kỹ thuật | `rag/bm25.py`, `rag/rerank.py`, `rag/grounding.py`, `rag/technical/store.py` | 4 test `*_unicode_normal_form` / `*_nfd_*` |
| Bộ chấm: phủ định có trợ từ chen giữa ("không được phép …") bị bỏ qua | `evals/graders.py` | `test_prohibited_action_is_not_counted_as_the_required_fact`, `test_safety_warning_is_not_a_forbidden_instruction` |
| Groundedness không thấy câu trả lời đảo nghĩa nguồn | `evals/graders.py` | `test_groundedness_rejects_an_answer_that_negates_its_source` |
| KPI "ca hoàn tất" nhảy sang ngày sửa cuối | `field/models.py` (cột `resolved_at` + listener), `schema_migrations.py` (migration 6, backfill từ `updated_at`), `pipeline/jobs/tasks.py` | `test_resolved_case_kpi_does_not_move_to_the_day_of_a_later_edit` |
| DOCX zip bomb | `rag/ingest.py`, `docs/router.py` | `test_docx_zip_bomb_is_rejected_before_extraction` |
| OCR không có trần output | `rag/llm.py` | `test_ocr_call_caps_output_tokens` |
| GET tốn tiền LLM không cần CSRF | `dependencies.py`, `docs/router.py` | `test_suggested_questions_requires_csrf_header_for_cookie_sessions` |
| URL upload hỏng khi API_URL không có scheme | `frontend/lib/api.ts`, `frontend/components/UploadZone.tsx` | typecheck + build |
| Trang `/docs` ghi nhầm là tài liệu của đội | `frontend/app/(app)/docs/page.tsx`, `layout.tsx`, `components/Sidebar.tsx` | kiểm bằng trình duyệt |

Test migration (`test_schema_migrations.py`, `test_postgres_migrations.py`) đã được cập nhật bộ phiên bản mong đợi thành `(1, …, 6)`.

---

## 8. Còn lại và lý do chưa làm

| Việc | Vì sao chưa làm |
|---|---|
| Technical RAG: cắt top-N thay vì từ chối khi có quá nhiều bằng chứng; chấp nhận dấu phẩy thập phân | Cả hai là **quyết định an toàn có chủ đích** trong mã ("whole required parents", "confirm the original notation"). Đổi là đổi hành vi sản phẩm, cần chủ sản phẩm quyết định. |
| Rerank từ vựng thay bằng giữ thứ tự RRF; gộp token không dấu | Đổi thứ hạng truy hồi; cần eval trên corpus lớn hơn để biết tốt lên hay xấu đi. |
| Holdout eval lớn hơn, do chuyên gia duyệt, đóng băng | Cần dữ liệu và người duyệt nghiệp vụ, không phải việc sửa mã. |
| Cookie cross-site trên Render (`onrender.com`) | Lựa chọn hạ tầng: proxy cùng origin hay domain riêng. Cần chủ hệ thống chọn. |
| Thu hồi token / giới hạn gia hạn phiên | Đổi hành vi đăng nhập (có thể đăng xuất người dùng hiện tại); cần quyết định chính sách phiên. |
| Đưa `enforce_budget` ra threadpool; dispatcher webhook dùng stream + deadline tổng; dọn rò SSE | Sửa ở lõi đồng thời, nên làm thành một thay đổi riêng có test tải. |
| Giới hạn input theo ký tự/2.5 hoặc `count_tokens` | Làm giảm độ dài prompt cho phép, tức đổi hành vi; cần đo trên dữ liệu thật trước. |
| Backoff webhook 60 s → 30 s, thêm jitter, 4xx dead ngay | Nhỏ; đổi lịch thử lại mà đối tác CRM có thể đang dựa vào. |
| Tính năng sản phẩm ở mục 1 | Là roadmap, không phải lỗi. |

---

## 9. Kết quả kiểm thử cuối

Chạy trên cây làm việc, trước commit:

| Bộ | Lệnh | Kết quả |
|---|---|---|
| Backend pytest (WSL, Python 3.12) | `pytest tests -q` | **551 passed, 12 skipped** (các test cần PostgreSQL thật bị skip) |
| Test hồi quy vòng 5 trên `ddf8352` | `pytest tests/test_audit_regressions_round5.py` | **13 failed, 1 passed** (đúng kỳ vọng) |
| Client state (Node) | `node --test tests/client_state.test.cjs` | **20/20 pass** |
| Frontend | `tsc --noEmit` + `next build` | **0 lỗi; build thành công** (19 trang tĩnh) |
| Mobile | `tsc --noEmit` | **0 lỗi** |
| Eval template | `run_rag_evaluation.py --mode validate-only` | validated |
| Cổng truy hồi BM25 | `run_eval_benchmark.py --offline --fail-on-regression` | 16/16, recall@k 1.0, MRR 1.0 |
| Eval offline đầy đủ | `run_evals.py --mode offline` | 68 câu, pass 0.7206, recall@5 1.0, MRR 0.9121, phủ dữ kiện 0.7727, từ chối đúng 0.6923, 0 vi phạm — **trùng khớp baseline** |
| QA HTTP thật | `qa_flow.py` | **17/17** |
