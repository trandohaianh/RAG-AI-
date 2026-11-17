# FieldOps — Đánh giá đa vai trò và thuật toán (vòng 5 + vòng 6)

> **Cập nhật vòng 6 (cùng ngày):** mọi mục "còn lại" ở mục 8 đã được xử lý. Các mục cần ý kiến đã được hỏi chủ sản phẩm và làm theo phương án được chọn. Xem **mục 10** (quyết định, lý do, cách sửa) và **mục 11** (kết quả kiểm thử cuối).

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

## 8. Còn lại và lý do chưa làm (sau vòng 5 — **đã xử lý ở vòng 6, xem mục 10**)

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

---

## 10. Vòng 6 — sửa nốt và các quyết định của chủ sản phẩm

- **Mốc so sánh:** `b767f92`.
- **Test hồi quy:** `tests/test_audit_regressions_round6.py` (28 test), 3 test mới trong `tests/client_state.test.cjs`, 1 test mới trong `tests/test_edge_config_parity.py`.

### 10.1 Các mục cần ý kiến — phương án đã chọn và vì sao

Chủ sản phẩm được hỏi một lần. Mỗi câu có mô tả cụ thể, đánh đổi và phương án khuyến nghị. Chủ sản phẩm chọn phương án khuyến nghị ở **cả bốn câu**.

| Vấn đề | Phương án đưa ra | Đã chọn | Vì sao chọn | Cách sửa |
|---|---|---|---|---|
| Technical RAG từ chối khi có hơn 4 mục liên quan, và từ chối mọi số thập phân dấu phẩy ("12,5 V") | (a) Giữ top-4 và đọc dấu phẩy là thập phân; (b) chỉ giữ top-4; (c) giữ nguyên | **(a)** | Từ chối đúng lúc bằng chứng dồi dào là ngược đời. Dấu phẩy là cách viết thập phân bình thường của tiếng Việt. Đánh đổi: "1,500 W" (phân cách hàng nghìn) bị đọc là 1.5. Trường hợp này hiếm trong SOP có đơn vị, và vẫn khớp nếu hai bên viết giống nhau. | `rag/technical/pipeline.py`: `_quantity()` chuẩn hoá cả câu hỏi lẫn tài liệu ("," → "."). `select` giữ các mục điểm cao nhất trong giới hạn `max_parents` và ngân sách ký tự; mỗi mục vẫn nguyên vẹn. Chỉ từ chối khi ngay mục tốt nhất cũng không vừa. |
| Đăng nhập trên Render bị quay vòng về /login (web và API ở hai site `onrender.com` khác nhau) | (a) Proxy cùng origin `/api`; (b) domain riêng; (c) bearer trong localStorage | **(a)** | Cookie thành first-party nên chạy được cả trên Safari/Firefox. Cùng mô hình với bản deploy Caddy, không cần mua domain. Không mở lại rủi ro XSS lấy token như (c). Đánh đổi: thêm một chặng qua server Next (vài ms). | Xem chi tiết ngay dưới bảng. |
| Token gia hạn được vô hạn; đăng xuất không thu hồi token | (a) Gia hạn giữ hạn gốc, đăng xuất thu hồi mọi thiết bị; (b) danh sách thu hồi theo từng token (jti); (c) giữ nguyên | **(a)** | Đóng cả hai lỗ hổng mà không cần bảng mới, không thêm truy vấn DB mỗi request. Đánh đổi: đăng xuất trên web cũng đăng xuất app mobile. | Xem chi tiết ngay dưới bảng. |
| Ước lượng token theo ký tự/4 đếm thiếu tiếng Việt | Áp dụng hay không (câu chọn nhiều) | **Áp dụng** | Trần chi phí một request phải nghiêng về phía an toàn. Đánh đổi: prompt dài nhất cho phép ngắn đi khoảng 1/3. Với 5 chunk × 1000 ký tự cộng lịch sử thì vẫn còn rất xa trần. | `costs/recorder.py`: `CHARS_PER_TOKEN = 2.5`. Đặt trước ngân sách nạp tài liệu dùng cùng hằng số. |
| Rerank từ vựng vứt bỏ thứ tự hybrid | Áp dụng hay không | **Áp dụng** | Đã hứa: nếu eval offline tụt dưới ngưỡng thì hoàn tác và báo lại. Kết quả: **MRR tăng 0.9121 → 0.9636**, pass rate giữ 0.7206. | Xem chi tiết ngay dưới bảng. |
| Chính sách thử lại webhook | Áp dụng hay không | **Áp dụng** | Lần thử lại đầu khoảng 30 s, đúng như tài liệu. Jitter ±20% để không dội lại đối tác cùng một giây. Lỗi 4xx là lỗi của chính yêu cầu nên thử lại 8 lần là vô ích; vẫn gửi lại bằng tay được trong giao diện. | `field/integrations.py`: `_backoff` = 30·2^(n−1) ±20%. `_permanent_failure` (4xx trừ 408/429) cho vào dead-letter ngay. |
| Eval: holdout bị rò, quá nhỏ | Tách dev/holdout hay không | **Áp dụng** | Chỉnh tham số trên dev, chỉ chặn merge trên holdout đóng băng. Như vậy con số holdout mới đo được khả năng tổng quát hoá. | Xem dòng "Eval" ở 10.2. Phần **tăng holdout lên ≥100 câu do chuyên gia duyệt vẫn cần người nghiệp vụ**. Mã đã có trường `reviewed_by`/`reviewed_at` và bộ đếm (hiện 0/12). |

**Chi tiết cách sửa cho ba quyết định có thay đổi lớn:**

- **Proxy `/api` cùng origin (Render):**
  - `frontend/next.config.js`: thêm `rewrites` chuyển `/api/*` → `API_PROXY_TARGET`, tính lúc build.
  - `frontend/Dockerfile`: có `ARG API_PROXY_TARGET`.
  - `render.yaml`: đặt `NEXT_PUBLIC_API_URL=/api`, `API_PROXY_TARGET` lấy host của rag-api, thêm `API_PUBLIC_URL`.
  - `lib/api.ts`: giữ nguyên URL tương đối "/api" (trước đây bị biến thành `https:///api`).
  - `lib/offlineQueue.ts`: lấy origin của trang làm gốc cho URL tương đối.
- **Chính sách phiên:**
  - `/auth/session` cấp cookie với **đúng `exp` của token gốc** (`create_access_token(expires_at=…)`, `max_age` = thời gian còn lại).
  - `/auth/logout` tăng `token_version` khi request chứng minh được không phải CSRF: có bearer, hoặc cookie kèm header `X-Requested-With`.
  - Thiếu header thì chỉ xoá cookie như cũ, để một trang lạ không thể đăng xuất mọi thiết bị của nạn nhân.
- **Rerank giữ thứ tự hybrid (`rag/rerank.py`):**
  - Khi đầu vào là `HybridRanking` (RRF của BM25 + vector, đánh dấu ở `retriever.py`): gộp thứ hạng hybrid với thứ hạng độ trùng từ bằng RRF k=60; hoà điểm thì giữ thứ tự hybrid.
  - Khi nhánh BM25 hỏng (chỉ còn vector): độ trùng từ là tín hiệu từ vựng duy nhất, nên vẫn xếp lại toàn bộ như cũ. Điều này giữ được test hồi quy "đèn LOS đỏ không ra mục an toàn lao động".

### 10.2 Các mục không cần ý kiến — đã sửa

| Vấn đề (file:dòng cũ) | Kịch bản lỗi | Cách sửa | Test (fail trên `b767f92`) |
|---|---|---|---|
| `enforce_budget` (DELETE+INSERT dưới khoá ghi SQLite) chạy thẳng trên event loop: `chat/router.py:211`, `embed/router.py:245`, `docs/router.py:62,185`, `field/router.py:697`, `public_api/router.py:140`, judge | Một lần chờ khoá (busy_timeout 30 s; container pipeline cùng ghi `app.db`) đóng băng cả worker uvicorn, kể cả `/health` | `enforce_budget_async` chạy trong threadpool; trả khoản đặt trước bằng `release_async`, được che chắn khỏi huỷ | `test_budget_reservation_never_runs_on_the_event_loop` |
| Client ngắt **trước** lần lặp đầu của SSE nên `finally` của generator không bao giờ chạy | Khoản đặt trước bị giữ tới hết TTL 15 phút, chặn oan các lượt hỏi khác | `budgeted_stream()`: response tự trả khoản đặt trước khi kết thúc, dù generator chưa chạy | `test_reservation_is_returned_when_the_client_leaves_before_the_first_chunk` (ASGI thật, client ngắt trước byte đầu) |
| Receiver webhook "nhỏ giọt" (`field/integrations.py:465`; timeout của httpx tính theo từng lần đọc) | Dispatcher kẹt vô hạn; quá `CLAIM_LEASE` thì delivery bị gửi trùng; các đội khác bị nghẽn | `_DeadlineClient`: mỗi POST chạy trong asyncio với hạn chót tổng `FIELD_WEBHOOK_DEADLINE_SECONDS` (20 s), chỉ đọc tối đa 4 KB phản hồi | `test_slow_loris_receiver_cannot_hold_the_dispatcher` (server TCP thật gửi 1 byte mỗi 0.2 s) |
| 8 slot stream chỉ được nhả khi luồng provider đọc xong (`rag/chain.py:19`) | 8 client ngắt giữa chừng làm RAG từ chối mọi người trên worker đó tới 60 s | Tách hai giới hạn: `_STREAM_SLOTS` (client đang được phục vụ, trả ngay khi ngắt) và `_STREAM_THREADS` = 32 (luồng provider còn sống, vẫn có trần) | `test_disconnected_client_returns_its_stream_slot_while_the_provider_read_continues`. Test cũ trong `test_rag_streaming.py` chuyển sang kiểm `_STREAM_THREADS`. |
| Kiểm trích dẫn tốn O(n²) theo số token (`chain.py:187-190`) | Mỗi token ghép lại cả câu trả lời và kiểm lại từ đầu; nặng với Ollama | Chỉ kiểm phần mới (`pending`); kiểm toàn bộ một lần khi xong | `test_citation_audit_work_is_linear_in_the_answer_length` |
| Lần đầu mở app tạo hai "Đội kỹ thuật" (`field/router.py:276-287`) | Hai tab, hoặc web + mobile, cùng gọi `/field/workspaces` | Khoá dòng user (PostgreSQL) hoặc khoá ghi (SQLite) rồi kiểm tra lại trước khi tạo | `test_concurrent_first_launch_creates_one_default_team` |
| Di trú token cũ đăng xuất người dùng khi offline (`lib/api.ts:54`, `lib/auth.tsx:72`) | Token bị xoá trước khi gọi `/auth/session`; mọi lỗi, kể cả mất mạng, đều xoá phiên | Chỉ xoá token khi server trả thành công hoặc 401/403. Mất mạng thì giữ token và hồ sơ đã lưu, thử lại lần sau | 2 test client mới |
| Caddy nén luồng SSE (`deploy/cloud/Caddyfile:6`) | `encode` mặc định khớp `text/*`, kể cả `text/event-stream` | Liệt kê tường minh các loại nội dung được nén, không gồm SSE | `test_caddy_never_compresses_the_sse_chat_stream` |
| KPI judge tính cả câu từ chối (faithfulness 1.0) | Hệ thống càng từ chối nhiều, "độ bám nguồn" càng đẹp | Không gửi câu từ chối cho judge (cũng đỡ tốn tiền) | `test_refusals_are_not_sent_to_the_judge` |
| p95 của KPI (nearest-rank) khác p95 của báo cáo eval (nội suy) | Cùng một dãy độ trễ ra hai con số khác nhau | KPI dùng `evals.graders.percentile` | `test_kpi_p95_matches_the_eval_report_definition` |
| Một chân trang "Trang 3" hay một khối lặp làm hỏng cả tài liệu ở bước kiểm chất lượng | PDF thật gần như luôn có chân trang | `drop_noise_chunks`: bỏ chunk dưới 10 ký tự và chunk trùng, ghi chú vào kết quả kiểm tra | `test_page_footers_and_repeated_blocks_do_not_fail_a_whole_document` |
| BM25 không khớp câu gõ không dấu; "E204" ≠ "E-204" | Thợ gõ trên điện thoại hay bỏ dấu | Thêm token bỏ dấu (bản có dấu vẫn giữ, nên câu gõ đúng dấu vẫn được điểm cao hơn). Chuẩn hoá mã E204 ↔ E-204, cả ở cổng khớp mã. | `test_unaccented_query_and_joined_error_code_match_the_sop` |
| Đặt trước ngân sách nạp tài liệu giả định "văn bản không dài hơn file" — sai với DOCX/PDF nén | Chi phí embedding vượt khoản đặt trước | `MAX_TEXT_CHARS_PER_BYTE = 5`: ingest từ chối văn bản dài hơn mức này, và khoản đặt trước tính theo đúng mức này | `test_compressed_document_cannot_extract_more_text_than_was_reserved` |
| Eval | Baseline chốt trên "all" nên gồm cả holdout; dung sai 0.03 nhỏ hơn 1 câu/12 | Xem chi tiết ngay dưới bảng | `test_holdout_is_frozen_by_fingerprint_and_dev_edits_do_not_touch_it`, `test_ci_gates_only_on_the_holdout_split` |

**Chi tiết sửa eval:**
- CI chạy split dev chỉ để báo cáo, và **chỉ chặn merge trên holdout**.
- Có hai baseline riêng: `offline-dev.json` và `offline-holdout.json`.
- Baseline holdout lưu `holdout_fingerprint`; sửa holdout thì CI báo.
- Dung sai tự nâng lên ít nhất 1/n (một câu).
- Đếm số câu holdout đã có chuyên gia duyệt.

**Test cũ được cập nhật theo các quyết định** (giá trị mong đợi thay đổi có chủ đích):
- `test_technical_rag.py::test_comma_decimal_abstains` đổi thành `test_comma_decimal_is_read_as_decimal`.
- Số token ước lượng trong `test_audit_regressions.py` và `test_cost_tracking.py` (giờ chia 2.5).
- p95 trong `test_pipeline_jobs.py`: 1500 → 1470.0 (nội suy tuyến tính).

### 10.3 Vẫn chưa làm — và vì sao

| Việc | Lý do |
|---|---|
| Tăng holdout lên ≥100 câu do chuyên gia nghiệp vụ viết và duyệt | Cần người làm nghề, không phải việc sửa mã. Cơ chế đóng băng, trường duyệt và bộ đếm đã có (`backend/evals/README.md`). |
| Điều phối: thêm relocate/swap giữa các tuyến; dùng OR-Tools khi trên khoảng 50 ca; dùng OSRM | Là cải tiến thuật toán, không phải lỗi. Cần bộ dữ liệu ca thật để chứng minh tốt hơn; nên làm thành một thay đổi riêng. |
| Chia chunk theo tiêu đề mục; dò lại TOP_K và số ứng viên; hiệu chuẩn `min_score` của technical RAG | Cần corpus thật lớn hơn khoảng 10 lần và có nhãn chuẩn. Dò trên 16 chunk hiện có chỉ là overfit. |
| Nhận IP thật của client sau proxy `/api` trên Render (rate limit theo IP) | Trên Render, API vốn chỉ thấy IP của load balancer vì uvicorn không tin `X-Forwarded-For`, nên đây không phải lỗi mới. Tin `*` thì cho phép giả mạo IP, vì rag-api vẫn công khai. Nên giải quyết khi có domain riêng hoặc mạng nội bộ. |
| Kiểm tra SSE qua rewrite của Next trên Render thật | Chỉ kiểm được bằng deploy thật. Build với `NEXT_PUBLIC_API_URL=/api API_PROXY_TARGET=…` đã sinh đúng luật rewrite trong `routes-manifest.json`. |
| Tính năng sản phẩm ở mục 1 (mobile "Lịch hôm nay", push, biên bản đóng ca, geocoding, giá theo đội) | Là roadmap, không phải lỗi. |

**Ghi chú về Next.js:** lần `next build` đầu tiên dừng lại vì công cụ Next.js báo phiên bản đang dùng có cảnh báo bảo mật đã công bố; chạy lại thì build thành công. Đã nâng cấp sau đó, xem mục 12.

---

## 11. Kết quả kiểm thử cuối (vòng 6)

| Bộ | Lệnh | Kết quả |
|---|---|---|
| Backend pytest (WSL, Python 3.12) | `pytest tests -q` | **580 passed, 12 skipped** (các test cần PostgreSQL thật) |
| Test hồi quy vòng 6, chạy trên `b767f92` | `pytest tests/test_audit_regressions_round6.py` | **27 failed, 1 passed.** Test pass là test bảo vệ: đăng xuất thiếu header CSRF chỉ xoá cookie, giống hành vi cũ. Đúng kỳ vọng. |
| Client state (Node) | `node --test tests/client_state.test.cjs` | **23/23 pass**; 3 test mới fail trên code cũ |
| Frontend | `tsc --noEmit` + `next build` (cả cấu hình thường và `/api` cùng origin) | **0 lỗi; build thành công.** Luật rewrite `/api/:path*` có trong `routes-manifest.json`. |
| Mobile | `tsc --noEmit` | **0 lỗi** (mobile không đổi) |
| Eval template | `run_rag_evaluation.py --mode validate-only` | validated |
| Cổng truy hồi BM25 | `run_eval_benchmark.py --offline --fail-on-regression` | 16/16, recall@k 1.0, MRR 1.0 |
| Eval offline — all | `run_evals.py --mode offline --split all` | Đạt. 68 câu, pass 0.7206, recall@5 1.0, **MRR 0.9636** (trước 0.9121), phủ dữ kiện 0.7727, từ chối đúng 0.6923, 0 vi phạm. |
| Eval offline — dev (chỉ báo cáo) | `--split dev --no-gates` | 56 câu, pass 0.6964, MRR 0.9674 |
| Eval offline — holdout (cổng CI) | `--split holdout` | **Đạt cổng.** 12 câu, pass 0.8333, MRR 0.9444, phủ dữ kiện 0.8889, 0 vi phạm. |

---

## 12. Nâng cấp Next.js (sau vòng 6, chủ sản phẩm đồng ý)

- **Advisory:** `npm audit` trên lockfile cũ báo **GHSA-mfr8-8qww-v76h**, mức *moderate*. Next.js có thể làm lộ thông tin giữa các root params khi một hàm `'use cache'` được gọi qua `unstable_cache()`. Advisory ảnh hưởng `next` 16.3.0 – 16.4.4.
- **Thay đổi:** `next` **16.4.2 → 16.4.5**. Đây là bản vá gần nhất trong cùng dòng 16.4; `npm audit fix --force` sẽ cài 16.4.6, nhưng 16.4.5 đã đủ để vá.
  - Lockfile chỉ đổi `next` và các gói `@next/env`, `@next/swc-*` mà `next` kéo theo đúng phiên bản.
  - Dự án không dùng `eslint-config-next`. Mobile (Expo) không dùng Next.
- **Kiểm tra sau nâng cấp:**

| Bộ | Kết quả |
|---|---|
| `npm audit` | **found 0 vulnerabilities** |
| `tsc --noEmit` | 0 lỗi |
| `next build` (bình thường) | thành công ngay lần đầu, không còn cảnh báo bảo mật |
| `next build` (`NEXT_PUBLIC_API_URL=/api`, `API_PROXY_TARGET=…`) | thành công; luật rewrite `/api/:path*` có trong `routes-manifest.json` |
| Client tests | 23/23 pass |
