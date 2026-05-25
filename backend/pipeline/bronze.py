"""Bronze layer — raw document metadata + text snapshot."""
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from pipeline.db import session_scope


def file_content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def load_raw_document(
    user_id: int,
    file_path: Path,
    raw_text: str,
    dag_run_id: str | None = None,
) -> int:
    content = file_path.read_bytes()
    content_hash = file_content_hash(content)
    suffix = file_path.suffix.lower().lstrip(".")

    with session_scope() as db:
        row = db.execute(
            text(
                """
                INSERT INTO bronze.raw_documents (
                    user_id, filename, file_path, file_size_bytes,
                    content_hash, raw_text_length, source_type, dag_run_id
                ) VALUES (
                    :user_id, :filename, :file_path, :file_size,
                    :content_hash, :text_len, :source_type, :dag_run_id
                )
                ON CONFLICT (user_id, filename, content_hash)
                DO UPDATE SET ingested_at = NOW(), dag_run_id = EXCLUDED.dag_run_id
                RETURNING id
                """
            ),
            {
                "user_id": user_id,
                "filename": file_path.name,
                "file_path": str(file_path),
                "file_size": len(content),
                "content_hash": content_hash,
                "text_len": len(raw_text),
                "source_type": suffix,
                "dag_run_id": dag_run_id,
            },
        ).fetchone()
        return int(row[0])


def start_ingest_run(dag_run_id: str) -> int:
    with session_scope() as db:
        row = db.execute(
            text(
                """
                INSERT INTO bronze.ingest_runs (dag_run_id, status)
                VALUES (:dag_run_id, 'running')
                RETURNING id
                """
            ),
            {"dag_run_id": dag_run_id},
        ).fetchone()
        return int(row[0])


def finish_ingest_run(
    run_id: int,
    status: str,
    files_processed: int,
    error_message: str | None = None,
) -> None:
    with session_scope() as db:
        db.execute(
            text(
                """
                UPDATE bronze.ingest_runs
                SET finished_at = :finished_at, status = :status,
                    files_processed = :files_processed, error_message = :error_message
                WHERE id = :id
                """
            ),
            {
                "id": run_id,
                "finished_at": datetime.now(timezone.utc),
                "status": status,
                "files_processed": files_processed,
                "error_message": error_message,
            },
        )
