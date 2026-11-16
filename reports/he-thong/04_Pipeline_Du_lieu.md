# Hệ thống 4: Pipeline dữ liệu — medallion, job vận hành, Airflow

**Phạm vi mã nguồn:**

- `backend/pipeline/` (`bronze.py`, `silver.py`, `gold.py`, `medallion.py`, `lineage.py`, `db.py`)
- `backend/pipeline/jobs/` (`runner.py`, `tasks.py`, `daily_ingest.py`, `router.py`)
- `backend/validation/ge_validate.py`
- `airflow/dags/daily_rag_ingest.py`
- `infra/postgres/init.sql`

---

## 1. Vai trò của hệ thống

Hệ thống có hai phần độc lập:

1. **Medallion (tuỳ chọn, PostgreSQL):** lưu metadata và nội dung tài liệu qua ba tầng.

   | Tầng | Nội dung |
   |---|---|
   | Bronze | Thô |
   | Silver | Chunk đã được Great Expectations kiểm tra |
   | Gold | Tổng hợp cho dashboard và lineage |

   Medallion tắt được khi chạy chỉ với SQLite.
2. **Job vận hành** (bảng `pipeline_runs`): `daily_kpis`, `data_quality`, `retention`, `backup`, và việc gửi lại webhook ra.
   - Chạy trong container `pipeline`, là một scheduler tối giản.
   - Hoặc chạy qua Airflow (phương án thay thế, không bật cùng lúc).

---

## 2. Luồng xử lý

### 2.1 Medallion cho một tệp (`run_medallion_for_file`)

**1. Bronze**

- `load_raw_document` upsert theo `(user_id, filename, content_hash)`.
- Nạp lại cùng nội dung chỉ tạo một dòng.

**2. Validate**

- Tách chunk giống hệt luồng RAG.
- Kiểm tra bằng Great Expectations: chunk không rỗng, độ dài tối thiểu, không trùng nội dung.

**3. Silver** (`write_silver_chunks`)

- Xoá chunk cũ của nguồn, rồi `INSERT … ON CONFLICT (user_id, source, content_hash) DO NOTHING`.
- **Số chunk ghi được lấy theo `rowcount` thật (F19).** Trước đây code đếm cả các dòng bị `ON CONFLICT` bỏ qua.

**4. Gold, tóm tắt tài liệu** (`refresh_document_summary`, F18)

- **Luôn** chạy sau mỗi lần nạp: `DELETE` dòng cũ rồi `INSERT … SELECT` các chunk silver đạt kiểm tra, trong cùng transaction.
- Nếu lần nạp lại không đạt kiểm tra, dòng gold cũ bị gỡ. Lineage không còn báo `gold_ready = True` với số chunk của phiên bản trước.

**5. Gold, thống kê ngày** (`upsert_daily_stats`)

- Tính lại toàn bộ một ngày từ bronze và silver, rồi ghi đè dòng của ngày đó, nên chạy lại vẫn ra cùng kết quả (commit `7569b27`).
- **Ngày được tính theo UTC (đã sửa):** tham số mặc định là `datetime.now(timezone.utc).date()` thay vì `date.today()` theo giờ máy, và mọi phép so được viết thành `CAST(col AT TIME ZONE 'UTC' AS DATE)`. Trước đây số liệu lệch sang ngày bên cạnh quanh nửa đêm nếu máy chủ hoặc session DB không ở UTC.

Ví dụ (giả định các cột thời gian lưu ở UTC):

- Một tệp nạp lúc 23:30 UTC ngày 15 trên máy chủ chạy UTC+7. `date.today()` trả về ngày 16, nhưng tệp được ghi nhận ngày 15, nên thống kê ngày 16 bỏ sót tệp đó.
- Nếu session DB chạy UTC+7 thì có thêm lệch thứ hai: `CAST(ingested_at AS DATE)` cho ngày 16 (06:30 giờ địa phương).

### 2.2 Ingest hằng ngày (`pipeline/jobs/daily_ingest.py`, U15, đã sửa)

| Kết quả | Trạng thái |
|---|---|
| Mọi tệp thành công | `success` |
| Có tệp lỗi | `partial` |
| **Mọi tệp đều lỗi** | `failed`, và tiến trình **thoát với mã khác 0**. Trước đây là `partial` với exit 0, nên retry của Airflow không bao giờ chạy |

Lỗi xảy ra ngoài vòng lặp (ví dụ khi quét thư mục) cũng đóng `bronze.ingest_runs` với trạng thái `failed`, không để kẹt ở `running`.

### 2.3 Job runner (`pipeline/jobs/runner.py`, U8, đã sửa)

**`run_job`**

- Ghi dòng `running`, chạy job, rồi ghi kết quả.
- Lệnh ghi đầu tiên giờ nằm trong `try`: DB mất kết nối trả về `fail` thay vì làm chết scheduler.

**`run_chain`**

- Toàn bộ chuỗi chạy dưới **khoá file** `storage_lock(CHROMA_DIR, "__pipeline_chain__")`, trên volume data dùng chung giữa API và container pipeline.
- Trước đây khoá chỉ là `threading.Lock` trong API. Một lượt chạy tay trùng với lịch làm `daily_kpis` chèn trùng `AnalyticsDaily` (IntegrityError) và hai lượt backup chồng lên nhau.

**`schedule_forever`**

1. **Khi khởi động:**
   - `fail_interrupted_runs` đánh dấu `fail` các lượt còn `running` quá 6 giờ (tiến trình bị kill giữa chừng).
   - Mốc **chạy bù** giờ dựa trên `backup`, job chỉ chạy trong chuỗi đầy đủ. Trước đây mốc là `daily_kpis`, mà job này chạy mỗi giờ nên chạy bù không bao giờ xảy ra.
2. **Vòng lặp mỗi 30 s:**
   - chạy chuỗi hằng ngày lúc `PIPELINE_DAILY_AT` (UTC);
   - `daily_kpis` và `data_quality` mỗi 60 phút;
   - `dispatch_due` cho webhook ra (đã claim nguyên tử, xem Hệ thống 2).
   - Mỗi chuỗi được bọc trong `_safe_chain`, nên lỗi tạm thời không làm dừng vòng lặp.

### 2.4 Airflow (`airflow/dags/daily_rag_ingest.py`, đã sửa)

- Đường dẫn backend lấy từ biến môi trường `FIELDOPS_BACKEND_DIR`; mặc định `/opt/rag/backend`.
- Chuỗi vận hành dùng `retries=0`, để một job lỗi không làm chạy lại cả backup.
- Docstring ghi rõ DAG này là **phương án thay thế** cho container pipeline và không được bật cùng lúc.
- Repo không kèm image Airflow.

---

## 3. Lỗi đã sửa

| ID | Mức | Vị trí | Kịch bản lỗi trước khi sửa | Test |
|---|---|---|---|---|
| F18 | TB–Thấp | `gold.py`, `medallion.py` | Nạp lại không đạt kiểm tra mà gold vẫn báo dữ liệu cũ. | `test_failed_revalidation_clears_the_gold_summary` |
| F19 | Thấp | `silver.py` | Đếm cả chunk bị bỏ qua do trùng. | `test_silver_count_excludes_rows_skipped_by_on_conflict` |
| U15a | Thấp | `gold.py:upsert_daily_stats` | Số liệu ngày lệch theo giờ máy hoặc múi giờ session. | `round2::test_gold_daily_stats_bucket_by_utc_day` |
| U15b | Thấp | `daily_ingest.py` | Mọi tệp lỗi vẫn exit 0; lỗi ngoài vòng lặp để `ingest_runs` kẹt ở `running`. | `round2::test_daily_ingest_fails_loudly_when_every_file_fails` |
| U8 | Thấp–TB | `runner.py` | Lượt chạy kẹt `running`; DB lỗi làm chết scheduler; chạy bù không kích hoạt; hai tiến trình chạy chuỗi song song. | `round2::test_interrupted_pipeline_runs_are_failed_and_db_outage_does_not_raise` (khoá file và mốc chạy bù được kiểm bằng đọc code) |
| U11b | Thấp | `costs/router.py`, `analytics/service.py` (`database.utc_date`) | Bucket theo ngày trên PostgreSQL dùng múi giờ của session DB. | `round2::test_daily_buckets_use_utc_on_postgresql` |

---

## 4. Hạn chế còn lại

- **Test PostgreSQL chưa chạy:** 12 test cần `FIELDOPS_TEST_POSTGRES_URL` bị skip trên máy kiểm toán vì Docker daemon không chạy. Các phần SQL mới (`AT TIME ZONE 'UTC'`, `DELETE` trong gold) được kiểm bằng cách bắt câu SQL sinh ra, chưa chạy trên PostgreSQL thật. CI có service Postgres và sẽ chạy các test này.
- **Khoá file:** cần volume data dùng chung giữa các tiến trình chạy job, đúng như `deploy/cloud`. Nếu chạy job trên máy khác thì khoá không có tác dụng.

---

## 5. Vùng đã xác minh là đúng

- Mọi câu SQL đều có tham số bind; không ghép chuỗi.
- Upsert bronze và chạy lại `daily_kpis` vẫn ra cùng kết quả (test hiện có).
- Backup SQLite dùng online backup API, và phần dọn bản cũ theo số lượng giữ lại là đúng.
