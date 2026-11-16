"""
Job ingest hàng ngày — gọi từ Airflow DAG hoặc CLI:
  python -m pipeline.jobs.daily_ingest
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Đảm bảo import backend modules
BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from config import UPLOAD_DIR  # noqa: E402
from pipeline.bronze import finish_ingest_run, start_ingest_run  # noqa: E402
from pipeline.db import init_medallion_schema, is_medallion_enabled  # noqa: E402
from pipeline.medallion import discover_upload_files  # noqa: E402
from rag.ingest import ingest_file  # noqa: E402


async def _run(dag_run_id: str) -> int:
    if not is_medallion_enabled():
        print("MEDALLION_ENABLED=false — bỏ qua job")
        return 0

    init_medallion_schema()
    run_id = start_ingest_run(dag_run_id)
    processed = 0
    errors: list[str] = []

    try:
        for user_id, file_path in discover_upload_files(UPLOAD_DIR):
            try:
                await ingest_file(user_id, file_path, dag_run_id=dag_run_id)
                processed += 1
                print(f"OK user={user_id} file={file_path.name}")
            except Exception as exc:
                errors.append(f"{file_path}: {exc}")
                print(f"FAIL {file_path}: {exc}")
    except Exception as exc:
        # Lỗi ngoài vòng lặp (vd. quét thư mục upload): không để bronze.ingest_runs kẹt 'running'.
        finish_ingest_run(run_id, status="failed", files_processed=processed,
                          error_message=f"{type(exc).__name__}: {exc}"[:500])
        raise

    status = ingest_status(processed, len(errors))
    finish_ingest_run(
        run_id,
        status=status,
        files_processed=processed,
        error_message="; ".join(errors[:5]) if errors else None,
    )
    if status == "failed":
        # Exit code khác 0 để Airflow/cron thử lại và báo lỗi, thay vì ghi "partial" rồi coi là xong.
        raise RuntimeError(f"Daily ingest failed for all {len(errors)} file(s)")
    return processed


def ingest_status(processed: int, failed: int) -> str:
    if not failed:
        return "success"
    return "partial" if processed else "failed"


def main() -> None:
    dag_run_id = f"daily_{datetime.now(timezone.utc).strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
    count = asyncio.run(_run(dag_run_id))
    print(f"Daily ingest hoàn tất: {count} file(s)")


if __name__ == "__main__":
    main()
