"""Gold layer — aggregates cho analytics / dashboard."""
from datetime import date, datetime, timezone

from sqlalchemy import text

from pipeline.db import session_scope


def refresh_document_summary(user_id: int, source: str) -> None:
    with session_scope() as db:
        db.execute(
            text(
                """
                INSERT INTO gold.document_summary (user_id, source, chunk_count, total_chars, last_ingested_at)
                SELECT
                    user_id,
                    source,
                    COUNT(*)::int,
                    COALESCE(SUM(char_count), 0)::bigint,
                    MAX(created_at)
                FROM silver.document_chunks
                WHERE user_id = :user_id AND source = :source AND validation_passed = TRUE
                GROUP BY user_id, source
                ON CONFLICT (user_id, source) DO UPDATE SET
                    chunk_count = EXCLUDED.chunk_count,
                    total_chars = EXCLUDED.total_chars,
                    last_ingested_at = EXCLUDED.last_ingested_at
                """
            ),
            {"user_id": user_id, "source": source},
        )


def upsert_daily_stats(metric_date: date | None = None) -> dict:
    """Tính lại số liệu của MỘT ngày từ bronze/silver rồi ghi đè dòng của ngày đó.

    Idempotent: chạy lại cùng tệp, Airflow thử lại (retries) hay chạy bù đều cho cùng kết quả.
    Bản cũ cộng dồn (files_processed = cũ + mới) nên mỗi lần thử lại làm số tệp trong ngày tăng gấp đôi,
    trong khi bronze không nhân đôi nhờ ON CONFLICT (user_id, filename, content_hash).

    - files_processed: số tệp bronze được nạp (hoặc nạp lại) trong ngày; nạp lại cùng nội dung vẫn là 1 dòng.
    - chunks_created: số chunk silver tạo trong ngày (silver xoá rồi ghi lại theo nguồn, nên chỉ đếm bản hiện hành).
    - validation_failures: số tệp có chunk không đạt kiểm tra chất lượng.
    - users_active: số người dùng có tệp được nạp trong ngày.
    """
    day = metric_date or date.today()
    with session_scope() as db:
        row = db.execute(
            text(
                """
                INSERT INTO gold.daily_ingest_stats (
                    metric_date, files_processed, chunks_created,
                    validation_failures, users_active, updated_at
                )
                SELECT
                    CAST(:metric_date AS DATE),
                    (SELECT COUNT(*) FROM bronze.raw_documents
                      WHERE CAST(ingested_at AS DATE) = CAST(:metric_date AS DATE)),
                    (SELECT COUNT(*) FROM silver.document_chunks
                      WHERE CAST(created_at AS DATE) = CAST(:metric_date AS DATE)),
                    (SELECT COUNT(DISTINCT bronze_id) FROM silver.document_chunks
                      WHERE CAST(created_at AS DATE) = CAST(:metric_date AS DATE)
                        AND validation_passed = FALSE),
                    (SELECT COUNT(DISTINCT user_id) FROM bronze.raw_documents
                      WHERE CAST(ingested_at AS DATE) = CAST(:metric_date AS DATE)),
                    :updated_at
                ON CONFLICT (metric_date) DO UPDATE SET
                    files_processed = EXCLUDED.files_processed,
                    chunks_created = EXCLUDED.chunks_created,
                    validation_failures = EXCLUDED.validation_failures,
                    users_active = EXCLUDED.users_active,
                    updated_at = EXCLUDED.updated_at
                RETURNING files_processed, chunks_created, validation_failures, users_active
                """
            ),
            {"metric_date": day, "updated_at": datetime.now(timezone.utc)},
        ).one()
        return {"metric_date": day.isoformat(), "files_processed": row[0], "chunks_created": row[1],
                "validation_failures": row[2], "users_active": row[3]}
