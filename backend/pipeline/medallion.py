"""Orchestrate Bronze → GE validate → Silver → Gold."""

from __future__ import annotations

from pathlib import Path

from pipeline.bronze import load_raw_document
from pipeline.db import is_medallion_enabled
from pipeline.gold import refresh_document_summary, upsert_daily_stats
from pipeline.silver import write_silver_chunks
from rag.ingest import _load_sync, _split_sync
from validation.ge_validate import chunks_to_dataframe, drop_noise_chunks, validate_document_chunks


def run_medallion_for_file(
    user_id: int,
    file_path: Path,
    dag_run_id: str | None = None,
) -> dict:
    """
    Chạy medallion pipeline (không embed Chroma).
    Trả về dict với bronze_id, validation, chunk_count.
    """
    if not is_medallion_enabled():
        return {"skipped": True, "reason": "medallion_disabled"}

    documents = _load_sync(file_path)
    raw_text = "\n".join(d.page_content for d in documents)
    bronze_id = load_raw_document(user_id, file_path, raw_text, dag_run_id=dag_run_id)

    split_docs = _split_sync(documents)
    df = chunks_to_dataframe(
        [
            {
                "content": d.page_content,
                "source": file_path.name,
                "chunk_index": i,
            }
            for i, d in enumerate(split_docs)
        ]
    )
    df, notes = drop_noise_chunks(df)
    validation = validate_document_chunks(df)
    validation.expectation_results.extend(notes)

    chunk_rows = df.to_dict("records")
    silver_count = write_silver_chunks(
        bronze_id=bronze_id,
        user_id=user_id,
        source=file_path.name,
        chunks=chunk_rows,
        validation=validation,
    )

    # Luôn đồng bộ gold: khi kiểm tra thất bại, dòng tóm tắt cũ phải bị gỡ.
    refresh_document_summary(user_id, file_path.name)

    upsert_daily_stats()  # tính lại cả ngày từ bronze/silver: chạy lại không đếm trùng

    return {
        "bronze_id": bronze_id,
        "validation": validation.to_dict(),
        "silver_chunks": silver_count,
        "success": validation.success,
    }


def discover_upload_files(upload_root: Path) -> list[tuple[int, Path]]:
    """Quét data/uploads/{user_id}/*.pdf|docx|txt."""
    found: list[tuple[int, Path]] = []
    if not upload_root.exists():
        return found
    for user_dir in upload_root.iterdir():
        if not user_dir.is_dir():
            continue
        try:
            user_id = int(user_dir.name)
        except ValueError:
            continue
        for f in user_dir.iterdir():
            if f.is_file() and f.suffix.lower() in {
                ".pdf",
                ".docx",
                ".txt",
                ".png",
                ".jpg",
                ".jpeg",
                ".webp",
                ".gif",
            }:
                found.append((user_id, f))
    return found
