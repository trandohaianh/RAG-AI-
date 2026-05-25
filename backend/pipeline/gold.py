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


def upsert_daily_stats(
    files_processed: int,
    chunks_created: int,
    validation_failures: int,
    users_active: int,
) -> None:
    today = date.today()
    with session_scope() as db:
        db.execute(
            text(
                """
                INSERT INTO gold.daily_ingest_stats (
                    metric_date, files_processed, chunks_created,
                    validation_failures, users_active, updated_at
                ) VALUES (
                    :metric_date, :files_processed, :chunks_created,
                    :validation_failures, :users_active, :updated_at
                )
                ON CONFLICT (metric_date) DO UPDATE SET
                    files_processed = gold.daily_ingest_stats.files_processed + EXCLUDED.files_processed,
                    chunks_created = gold.daily_ingest_stats.chunks_created + EXCLUDED.chunks_created,
                    validation_failures = gold.daily_ingest_stats.validation_failures + EXCLUDED.validation_failures,
                    users_active = GREATEST(gold.daily_ingest_stats.users_active, EXCLUDED.users_active),
                    updated_at = EXCLUDED.updated_at
                """
            ),
            {
                "metric_date": today,
                "files_processed": files_processed,
                "chunks_created": chunks_created,
                "validation_failures": validation_failures,
                "users_active": users_active,
                "updated_at": datetime.now(timezone.utc),
            },
        )
