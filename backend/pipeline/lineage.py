"""Tra cứu trạng thái pipeline Bronze/Silver/Gold cho từng tài liệu — phục vụ audit trail.

Đọc trực tiếp Postgres warehouse tại thời điểm request (khác với medallion.py chỉ
chạy lúc ingest). Nếu pipeline chưa bật hoặc Postgres không kết nối được, trả về
trạng thái rõ ràng thay vì bịa dữ liệu.
"""

from sqlalchemy import text

from pipeline.db import is_medallion_enabled, session_scope


def get_lineage_map(user_id: int) -> dict[str, dict]:
    """Trả về {filename: lineage} cho toàn bộ tài liệu của user trong 1 lượt query."""
    if not is_medallion_enabled():
        return {"__status__": {"medallion_enabled": False}}

    try:
        with session_scope() as db:
            bronze_rows = (
                db.execute(
                    text(
                        """
                    SELECT DISTINCT ON (filename) filename, ingested_at, content_hash
                    FROM bronze.raw_documents
                    WHERE user_id = :uid
                    ORDER BY filename, ingested_at DESC
                    """
                    ),
                    {"uid": user_id},
                )
                .mappings()
                .all()
            )

            silver_rows = (
                db.execute(
                    text(
                        """
                    SELECT source,
                           COUNT(*) AS chunk_count,
                           BOOL_AND(validation_passed) AS all_passed,
                           MAX(validated_at) AS validated_at
                    FROM silver.document_chunks
                    WHERE user_id = :uid
                    GROUP BY source
                    """
                    ),
                    {"uid": user_id},
                )
                .mappings()
                .all()
            )

            gold_rows = (
                db.execute(
                    text(
                        """
                    SELECT source, chunk_count, last_ingested_at
                    FROM gold.document_summary
                    WHERE user_id = :uid
                    """
                    ),
                    {"uid": user_id},
                )
                .mappings()
                .all()
            )
    except Exception:
        return {"__status__": {"medallion_enabled": True, "reachable": False}}

    silver_by_source = {r["source"]: r for r in silver_rows}
    gold_by_source = {r["source"]: r for r in gold_rows}

    lineage: dict[str, dict] = {"__status__": {"medallion_enabled": True, "reachable": True}}
    for b in bronze_rows:
        name = b["filename"]
        s = silver_by_source.get(name)
        g = gold_by_source.get(name)
        lineage[name] = {
            "bronze_ingested_at": b["ingested_at"].isoformat() if b["ingested_at"] else None,
            "silver_chunk_count": s["chunk_count"] if s else 0,
            "validation_passed": bool(s["all_passed"]) if s else None,
            "gold_ready": g is not None,
            "gold_chunk_count": g["chunk_count"] if g else None,
        }
    return lineage
