"""Silver layer — cleaned, validated chunks."""

from datetime import UTC, datetime

from sqlalchemy import text

from pipeline.db import session_scope
from validation.ge_validate import ValidationResult


def write_silver_chunks(
    bronze_id: int,
    user_id: int,
    source: str,
    chunks: list[dict],
    validation: ValidationResult,
) -> int:
    now = datetime.now(UTC)
    passed = validation.success
    written = 0

    with session_scope() as db:
        db.execute(
            text("DELETE FROM silver.document_chunks WHERE user_id = :uid AND source = :src"),
            {"uid": user_id, "src": source},
        )
        for c in chunks:
            content = (c.get("content") or "").strip()
            if not content:
                continue
            result = db.execute(
                text(
                    """
                    INSERT INTO silver.document_chunks (
                        bronze_id, user_id, source, chunk_index, content,
                        content_hash, char_count, validated_at, validation_passed
                    ) VALUES (
                        :bronze_id, :user_id, :source, :chunk_index, :content,
                        :content_hash, :char_count, :validated_at, :validation_passed
                    )
                    ON CONFLICT (user_id, source, content_hash) DO NOTHING
                    """
                ),
                {
                    "bronze_id": bronze_id,
                    "user_id": user_id,
                    "source": source,
                    "chunk_index": c.get("chunk_index", 0),
                    "content": content,
                    "content_hash": c.get("content_hash", ""),
                    "char_count": len(content),
                    "validated_at": now,
                    "validation_passed": passed,
                },
            )
            # ON CONFLICT DO NOTHING (chunk trùng nội dung) không ghi dòng nào: không được đếm.
            written += max(result.rowcount or 0, 0)
    return written
